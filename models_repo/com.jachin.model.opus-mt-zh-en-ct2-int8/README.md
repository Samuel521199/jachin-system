# OPUS-MT Chinese to English CT2 INT8

This package is a Jachin L1 model asset. It should contain a converted
CTranslate2 model under `model/`.

Prepare it from the repo root:

```powershell
python scripts\prepare_opus_mt_ct2_models.py --direction zh-en
```

After preparation, publish this model package from the L1 Capability Release
page. L3 installs it into:

```text
~/.jachin/models/com.jachin.model.opus-mt-zh-en-ct2-int8/
```
