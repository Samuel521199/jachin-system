# Qwen2.5 0.5B Instruct GGUF Q4_K_M

Small local instruction model used by Jachin English learning features to generate short, scene-aware example sentences.

The model file is installed under:

```text
~/.jachin/models/com.jachin.model.qwen2-5-0-5b-instruct-gguf-q4-k-m/model/Qwen2.5-0.5B-Instruct-Q4_K_M.gguf
```

Runtime consumers should treat this package as an optional local generation model. It is intended for background pre-generation and cache filling, not blocking foreground UI.
