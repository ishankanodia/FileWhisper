from setuptools import setup, find_packages

setup(
    name="filewhisper",
    version="0.1.0",
    description="Local RAG document assistant server with keyless LLM support.",
    packages=find_packages(),
    include_package_data=True,
    package_data={
        "filewhisper": ["static/*"],
    },
    entry_points={
        "console_scripts": [
            "filewhisper = filewhisper.server_launcher:main",
        ]
    },
    install_requires=[
        "fastapi",
        "uvicorn",
        "sentence-transformers",
        "faiss-cpu",
        "numpy",
        "python-dotenv",
        "langgraph",
        "PyMuPDF",
        "Pillow",
        "pytesseract",
        "easyocr",
    ]
)
