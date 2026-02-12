import backoff
from openai import OpenAI, APIError, APIConnectionError, RateLimitError, APITimeoutError
import os
import numpy as np
# os.environ["http_proxy"]="127.0.0.1:7890"
# os.environ["https_proxy"]="127.0.0.1:7890"

# Set API key from environment variable
api_key = os.getenv("OPENAI_API_KEY")
if not api_key:
    raise ValueError("OPENAI_API_KEY environment variable not set. Please set it before running.")

# Support for multiple API keys (optional)
api_list = [api_key] + [k.strip() for k in os.getenv("OPENAI_API_KEYS", "").split(",") if k.strip()]
api_nums = len(api_list)

class GPTAgent():
    def __init__(self, model):
        """
        :param model: The model to use for completion.
        """
        self.model = model

    @backoff.on_exception(
        backoff.fibo,
        # https://platform.openai.com/docs/guides/error-codes/python-library-error-types
        (
            APIError,
            APIConnectionError,
            RateLimitError,
            APITimeoutError,
        ),
    )
    def __call__(self, user_prompt, max_tokens, temperature, stop_words):
        """
        :param user_prompt: The user requirments.
        :param max_tokens: The maximum number of tokens to generate.
        :param temperature: The sampling temperature.
        :param stop: The stop sequence or a list of stop sequences.
        :return: Return the response and the usage of the model.
        """
        api_key = api_list[np.random.randint(0, api_nums)]
        client = OpenAI(api_key=api_key)
        response = client.completions.create(
            model=self.model,
            prompt=user_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            stop=stop_words
        )
        return response.choices[0].text, {
            "prompt_tokens": response.usage.prompt_tokens,
            "completion_tokens": response.usage.completion_tokens,
            "total_tokens": response.usage.total_tokens
        }


