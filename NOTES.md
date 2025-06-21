Set things up:
```
$ uv venv
Using CPython 3.13.3 interpreter at: /opt/homebrew/opt/python@3.13/bin/python3.13
Creating virtual environment at: .venv
Activate with: source .venv/bin/activate
$ source ./.venv/bin/activate
...
$ uv pip install -U -e .
...
$ gcloud auth login --update-adc
$ gcloud auth configure-docker us-docker.pkg.dev
...
```

Run:
```
$ xmanager launch ./examples/cifar10_tensorflow/launcher.py
```
