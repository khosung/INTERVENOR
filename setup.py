import os
from setuptools import setup, find_packages


def read_requirements():
    """Read requirements.txt and return as list"""
    with open(os.path.join(os.path.dirname(__file__), "requirements.txt")) as f:
        return [line.strip() for line in f if line.strip() and not line.startswith("#")]


setup(
    name="human-eval",
    py_modules=["human-eval"],
    version="1.0",
    description="",
    author="OpenAI",
    packages=find_packages(),
    install_requires=read_requirements(),
    entry_points={
        "console_scripts": [
            "evaluate_functional_correctness = human_eval.evaluate_functional_correctness:main",
        ]
    }
)
