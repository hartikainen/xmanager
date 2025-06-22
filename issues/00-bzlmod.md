As noted in the [Bazel documentation](https://bazel.build/external/migration):
> Bzlmod will replace the legacy WORKSPACE system. The WORKSPACE file is already disabled in Bazel 8 (late 2024) and will be removed in Bazel 9 (late 2025).

It would be nice to migrate the examples to Bzlmod. For many most of the examples, this should be trivial to do. For example, for the `local_arg_printer` example, just replacing `WORKSPACE` with the following `MODULE.bazel` should work:
```
module(
    name = "xmanager_local_arg_printer_example",
    version = "1.0",
)

bazel_dep(name = "rules_cc", version = "0.1.2")
```
