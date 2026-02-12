import json
from tqdm import tqdm
import openai
import backoff
import os
import numpy as np
from typing import Iterable, Dict
import gzip
import json
import os
import argparse
from agents.gpt.gpt_wrapper import GPTAgent

GEN_STOP_WORDS = {
    "python":['def'],

}


COR_STOP_WORDS = {
    "python":['def'],
}



def read_problems(problem_file: str) -> Dict[str, Dict]:
    return {task["task_id"]: task for task in stream_jsonl(problem_file)}

def read_results(result_file: str) -> Dict[str, Dict]:
    return {task["task_id"]: task for task in stream_jsonl(result_file)}

def stream_jsonl(filename: str) -> Iterable[Dict]:
    """
    Parses each jsonl line and yields it as a dictionary
    """
    if filename.endswith(".gz"):
        with open(filename, "rb") as gzfp:
            with gzip.open(gzfp, 'rt') as fp:
                for line in fp:
                    if any(not x.isspace() for x in line):
                        yield json.loads(line)
    else:
        with open(filename, "r") as fp:
            for line in fp:
                if any(not x.isspace() for x in line):
                    yield json.loads(line)

def write_jsonl(filename: str, data: Iterable[Dict], append: bool = False):
    """
    Writes an iterable of dictionaries to jsonl
    """
    if append:
        mode = 'ab'
    else:
        mode = 'wb'
    filename = os.path.expanduser(filename)
    if filename.endswith(".gz"):
        with open(filename, mode) as fp:
            with gzip.GzipFile(fileobj=fp, mode='wb') as gzfp:
                for x in data:
                    gzfp.write((json.dumps(x) + "\n").encode('utf-8'))
    else:
        with open(filename, mode) as fp:
            for x in data:
                fp.write((json.dumps(x) + "\n").encode('utf-8'))

def print_options(args,parser):
    message = 'Arguments:\n'
    for k, v in sorted(vars(args).items()):
        comment = ''
        default_value = parser.get_default(k)
        if v != default_value:
            comment = f'\t(default: {default_value})'
        message += f'{str(k):>30}: {str(v):<40}{comment}\n'

    print(message)


def code_generation(problems,args):
    generator = GPTAgent(args.model)
    stop = GEN_STOP_WORDS[args.language]
    samples = []
    for task_id in tqdm(problems, desc="Generating code", total=len(problems)):
        for _ in range(args.num_samples_per_task):
            code_signature = problems[task_id]["prompt"]
            # print("Task_id: ",task_id)
            # print(code_signature)
            response, usage = generator(code_signature, args.max_tokens, args.temperature, stop)
            # print("--------------------response-------------------")
            # print(response)
            sample = {"task_id": task_id, "completion": response}
            samples.append(sample)

    save_path = os.path.join(".", "results","code_generation",args.dataset, f"{args.language}.jsonl")
    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    print("Write the code generation results to file :\n",save_path)
    write_jsonl(save_path, samples)




def get_cor_prompt(buggy_code,error_message):
    with open('./prompt/code_teacher.txt', 'r') as f:
        prompt = f.read()

    prompt = prompt.replace("%%%buggy_code%%%",buggy_code)
    prompt = prompt.replace("%%%error_message%%%",error_message)
    return prompt

def cor_generation(problems,results,args, save_path=None):
    generator = GPTAgent(args.model)
    stop = COR_STOP_WORDS[args.language]
    samples = []
    for task_id in tqdm(problems, desc="Generate Chain-of-Repairing(CoR)", total=len(problems)):
        for _ in range(args.num_samples_per_task):
            if results[task_id]["result"] == "passed":
                sample = {"task_id": task_id, "completion": results[task_id]["completion"],"result":results[task_id]["result"],"passed":results[task_id]["passed"],"method": ""}
                samples.append(sample)
            else:
                code_signature = problems[task_id]["prompt"]
                buggy_code = code_signature + results[task_id]["completion"] + "\n"
                error_message = results[task_id]["result"]
                prompt = get_cor_prompt(buggy_code,error_message)
                # print(prompt)
                response, usage = generator(prompt, args.max_tokens, args.temperature, stop)
                # print("--------------------response-------------------")
                # print(response)
                sample = {"task_id": task_id, "completion": results[task_id]["completion"],"result":results[task_id]["result"],"passed":results[task_id]["passed"], "method": response}
                samples.append(sample)

    if save_path is None:
        save_path = os.path.join(".", "results", "cor_generation", args.dataset, f"{args.language}_cor.jsonl")
    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    print("Write the Chain-of-Repairing(CoR) to file :\n", save_path)
    write_jsonl(save_path, samples)

def get_repair_prompt(buggy_code,error_message,repair_method,code_signature):
    with open('./prompt/code_learner.txt', 'r') as f:
        prompt = f.read()

    prompt = prompt.replace("%%%buggy_code%%%", buggy_code)
    prompt = prompt.replace("%%%error_message%%%", error_message)
    prompt = prompt.replace("%%%repair_method%%%", repair_method)
    prompt = prompt +"\n\n"+code_signature
    return prompt

def iterative_repair(problems, args):
    """
    Implements the interactive repair loop (A^i -> B -> C -> repeat)
    A^i: Code generation/repair
    B: Test the code
    C: Generate CoR based on test results
    """
    import subprocess
    from pathlib import Path
    
    generator = GPTAgent(args.model)
    max_iterations = args.max_iterations if hasattr(args, 'max_iterations') else 3
    
    # Phase 1: Initial code generation
    print(f"\n{'='*60}")
    print(f"Starting Iterative Repair Loop (max {max_iterations} iterations)")
    print(f"{'='*60}\n")
    
    print("Phase 1: Initial code generation...")
    code_generation(problems, args)
    
    result_file = os.path.join(".", "results","code_generation",args.dataset, f"{args.language}.jsonl_results.jsonl")
    
    for iteration in range(1, max_iterations + 1):
        print(f"\n{'='*60}")
        print(f"Iteration {iteration}/{max_iterations}")
        print(f"{'='*60}")
        
        # Test current code
        print(f"\nTesting code...")
        subprocess.run([
            "evaluate_functional_correctness",
            os.path.join(".", "results", "code_generation", args.dataset, f"{args.language}.jsonl"),
            f"--problem_file={args.problem_file}"
        ])
        
        # Read results
        results = read_results(result_file)
        
        # Count passed/failed
        passed = sum(1 for r in results.values() if r.get("result") == "passed")
        failed = len(results) - passed
        
        print(f"\nResults: {passed} passed, {failed} failed")
        
        if failed == 0:
            print("\n✅ All tests passed! Repair loop completed.")
            break
        
        if iteration < max_iterations:
            # Phase 2: Generate CoR for failed tests
            print(f"\nPhase 2: Generating Chain-of-Repair (Iteration {iteration})...")
            cor_generation(problems, results, args)
            
            # Phase 3: Repair code
            print(f"\nPhase 3: Repairing code (Iteration {iteration})...")
            result_file_cor = os.path.join(".", "results", "cor_generation", args.dataset, f"{args.language}_cor.jsonl")
            results_cor = read_results(result_file_cor)
            
            # Read previous generation results
            repaired_samples = []
            for task_id in tqdm(problems, desc="Code Repairing", total=len(problems)):
                if results_cor[task_id]["result"] == "passed":
                    repaired_samples.append({
                        "task_id": task_id,
                        "completion": results_cor[task_id]["completion"],
                        "iteration": iteration
                    })
                else:
                    code_signature = problems[task_id]["prompt"]
                    buggy_code = code_signature + results_cor[task_id]["completion"] + "\n"
                    error_message = results_cor[task_id]["result"]
                    repair_method = results_cor[task_id]["method"]
                    prompt = get_repair_prompt(buggy_code, error_message, repair_method, code_signature)
                    response, usage = generator(prompt, args.max_tokens, args.temperature, GEN_STOP_WORDS[args.language])
                    repaired_samples.append({
                        "task_id": task_id,
                        "completion": response,
                        "iteration": iteration
                    })
            
            # Save repaired code for next iteration
            save_path = os.path.join(".", "results","code_generation",args.dataset, f"{args.language}.jsonl")
            write_jsonl(save_path, repaired_samples)
            print(f"Saved repaired code for iteration {iteration}")

def code_reapiring(problems,results,args, save_path=None):
    generator = GPTAgent(args.model)
    stop = GEN_STOP_WORDS[args.language]
    samples = []
    for task_id in tqdm(problems, desc="Code Repairing", total=len(problems)):
        for _ in range(args.num_samples_per_task):
            if results[task_id]["result"] == "passed":
                sample = {"task_id": task_id, "completion": results[task_id]["completion"],
                          "result": results[task_id]["result"], "passed": results[task_id]["passed"], "method": ""}
                samples.append(sample)
            else:
                code_signature = problems[task_id]["prompt"]
                buggy_code = code_signature + results[task_id]["completion"] + "\n"
                error_message = results[task_id]["result"]
                repair_method = results[task_id]["method"]
                prompt = get_repair_prompt(buggy_code,error_message,repair_method,code_signature)
                # print(prompt)
                response, usage = generator(prompt, args.max_tokens, args.temperature, stop)
                # print("--------------------response-------------------")
                # print(response)
                sample = {"task_id": task_id, "completion": response,
                          "result": results[task_id]["result"], "passed": results[task_id]["passed"],
                          "method": results[task_id]["method"]}
                samples.append(sample)

    if save_path is None:
        save_path = os.path.join(".", "results", "code_repair", args.dataset, f"{args.language}.jsonl")
    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    print("Write the code repair results to file :\n", save_path)
    write_jsonl(save_path, samples)


def interactive_repair(problems, args):
    """
    Implements the interactive repair loop as described in the paper.
    A^0: Initial code generation
    Then for each turn (max_turn times):
        B: Test the code and collect error messages
        C: Generate Chain-of-Repair (CoR) guidance
        A^i: Repair code based on CoR
    
    Each turn's repaired code becomes the input for the next turn.
    """
    import subprocess
    
    # Step A^0: Initial code generation
    print(f"\n{'='*70}")
    print(f"Interactive Repair Loop: Step A^0 (Initial Code Generation)")
    print(f"{'='*70}\n")
    
    gen_path = os.path.join(".", "results", "code_generation", args.dataset, f"{args.language}.jsonl")
    code_generation(problems, args)
    
    # Main repair loop
    for turn in range(1, args.max_turn + 1):
        print(f"\n{'='*70}")
        print(f"Interactive Repair Loop: Turn {turn}/{args.max_turn}")
        print(f"{'='*70}\n")
        
        # Step B: Test current code
        print(f"Step B (Turn {turn}): Testing code...")
        result_path = gen_path + "_results.jsonl"
        subprocess.run([
            "evaluate_functional_correctness",
            gen_path,
            f"--problem_file={args.problem_file}"
        ])
        
        # Read test results
        results = read_results(result_path)
        
        # Count passed/failed
        passed = sum(1 for r in results.values() if r.get("result") == "passed")
        failed = len(results) - passed
        pass_rate = (passed / len(results)) * 100 if results else 0
        
        print(f"\n✓ Turn {turn} Test Results: {passed}/{len(results)} passed (Pass@1: {pass_rate:.2f}%)")
        
        # Check if all passed
        if failed == 0:
            print(f"\n{'='*70}")
            print(f"✅ All tests passed! Interactive repair loop completed at Turn {turn}.")
            print(f"{'='*70}\n")
            break
        
        # Step C: Generate Chain-of-Repair (CoR)
        print(f"\nStep C (Turn {turn}): Generating Chain-of-Repair (CoR) by Code Teacher...")
        cor_path = os.path.join(".", "results", "interactive_repair", args.dataset, f"turn_{turn}_cor.jsonl")
        cor_generation(problems, results, args, save_path=cor_path)
        
        # Step A^i: Repair code based on CoR
        print(f"\nStep A^{turn}: Repairing code by Code Learner based on CoR...")
        results_cor = read_results(cor_path)
        repaired_path = os.path.join(".", "results", "interactive_repair", args.dataset, f"turn_{turn}_repaired.jsonl")
        code_reapiring(problems, results_cor, args, save_path=repaired_path)
        
        # Update gen_path for next iteration
        # The repaired code becomes the input for the next turn
        gen_path = repaired_path
        
        print(f"\n✓ Turn {turn} Complete: Repaired code saved to {repaired_path}")
    
    print(f"\n{'='*70}")
    print(f"Interactive Repair Loop Summary")
    print(f"{'='*70}")
    print(f"Max turns: {args.max_turn}")
    print(f"Final results saved in: ./results/interactive_repair/{args.dataset}/")
    print(f"{'='*70}\n")


def main():
    parser = argparse.ArgumentParser("Run the interactive loop.")
    parser.add_argument(
        "--problem_file",
        type=str,
        required=True,
        default="",
        help="The file containing the problems.",
    )
    parser.add_argument(
        "--dataset",
        type=str,
        default="humaneval",
        choices=["humaneval", "mbpp", "humanevalx", "codeerror"],
        help="The dataset to use.",
    )
    parser.add_argument(
        "--language",
        type=str,
        default="python",
        choices=["python", "cpp", "java", "js"],
        help="The language to use.",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="gpt-3.5-turbo-instruct-0914",
        help="The model to use.",
    )
    parser.add_argument(
        "--max_tokens",
        type=int,
        default="512",
        help="The maximum number of tokens to generate.",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default="0.2",
        help="The temperature to use.",
    )
    parser.add_argument(
        "--num_samples_per_task",
        type=int,
        default="1",
        help="The number of samples to generate per task.",
    )

    parser.add_argument(
        "--todo",
        type=str,
        choices=["code_generation", "cor_generation", "code_reapir", "interactive_repair"],
        required=True,
        default=None,
        help="Code generation or CoR generation or code repairing or interactive repair loop.",
    )
    parser.add_argument(
        "--max_turn",
        type=int,
        default=1,
        help="Maximum number of repair turns for interactive repair loop (A^i, B, C repetition).",
    )
    args = parser.parse_args()
    print_options(args,parser)
    problems = read_problems(args.problem_file)

    if args.todo == "code_generation":
        print("Start code generation...\n\n")
        code_generation(problems,args)
    elif args.todo == "cor_generation":
        print("Start CoR generation...\n\n")
        result_file = os.path.join(".", "results","code_generation",args.dataset, f"{args.language}.jsonl_results.jsonl")
        results = read_results(result_file)
        cor_generation(problems,results,args)
    elif args.todo == "code_reapir":
        print("Start code reapiring...\n\n")
        result_file = os.path.join(".", "results", "cor_generation", args.dataset, f"{args.language}_cor.jsonl")
        results = read_results(result_file)
        code_reapiring(problems,results,args)
    elif args.todo == "interactive_repair":
        print("Start Interactive Repair Loop...\n\n")
        interactive_repair(problems, args)
if __name__ == '__main__':
    main()
