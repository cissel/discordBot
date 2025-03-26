import torch
from transformers import pipeline

MODEL_NAME = "meta-llama/Llama-2-7b-chat-hf"

# Force LLaMA to run on CPU instead of MPS
device = torch.device("cpu")

llama_pipeline = pipeline(
    "text-generation",
    model="meta-llama/Llama-2-7b-chat-hf",
    model_kwargs={"torch_dtype": torch.float16},  # Reduce memory usage
    device=device  # ✅ Force CPU instead of MPS
)

prompt = "You are a helpful assistant. How can I help you today?"
response = llama_pipeline(prompt, max_length=100, num_return_sequences=1)

print("LLaMA Response:", response[0]["generated_text"])