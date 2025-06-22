```
$ cd ./examples/local_arg_printer
$ python ./launcher.py
I0622 13:41:56.244761 139799548634944 migration.py:155] Context impl SQLiteImpl.
I0622 13:41:56.244876 139799548634944 migration.py:158] Will assume non-transactional DDL.
I0622 13:41:56.245708 139799548634944 migration.py:155] Context impl SQLiteImpl.
I0622 13:41:56.245735 139799548634944 migration.py:158] Will assume non-transactional DDL.
INFO: Analyzed target //:arg_printer (0 packages loaded, 0 targets configured).
INFO: Found 1 target...
Target //:arg_printer up-to-date:
  bazel-bin/arg_printer
INFO: Elapsed time: 0.108s, Critical Path: 0.00s
INFO: 1 process: 1 internal.
INFO: Build completed successfully, 1 total action
INFO: Build Event Protocol files produced successfully.
W0622 13:41:56.460839 139799548634944 core.py:806] No work units were added to this experiment, which is usually not intended.
Traceback (most recent call last):
  File "/home/user/xmanager/examples/local_arg_printer/./launcher.py", line 56, in <module>
    app.run(main)
    ~~~~~~~^^^^^^
  File "/home/user/xmanager/.venv/lib/python3.13/site-packages/absl/app.py", line 316, in run
    _run_main(main, args)
    ~~~~~~~~~^^^^^^^^^^^^
  File "/home/user/xmanager/.venv/lib/python3.13/site-packages/absl/app.py", line 261, in _run_main
    sys.exit(main(argv))
             ~~~~^^^^^^
  File "/home/user/xmanager/examples/local_arg_printer/./launcher.py", line 36, in main
    [executable] = experiment.package(
                   ~~~~~~~~~~~~~~~~~~^
        [
        ^
    ...<6 lines>...
        ]
        ^
    )
    ^
  File "/home/user/xmanager/xmanager/xm/core.py", line 829, in package
    return cls._async_packager.package(packageables)
           ~~~~~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^
  File "/home/user/xmanager/xmanager/xm/async_packager.py", line 134, in package
    executables = self._package_batch(packageables)
  File "/home/user/xmanager/xmanager/xm_local/packaging/router.py", line 79, in package
    _packaging_router(built_targets, packageable)
    ~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/home/user/xmanager/xmanager/xm_local/packaging/router.py", line 38, in _packaging_router
    return local_packaging.package_for_local_executor(
           ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~^
        built_targets,
        ^^^^^^^^^^^^^^
        packageable,
        ^^^^^^^^^^^^
        packageable.executable_spec,
        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^
    )
    ^
  File "/home/user/xmanager/xmanager/xm_local/packaging/local.py", line 153, in package_for_local_executor
    return _package_bazel_binary(bazel_outputs, packageable, bazel_binary)
  File "/home/user/xmanager/xmanager/xm_local/packaging/local.py", line 137, in _package_bazel_binary
    assert len(paths) == 1
           ^^^^^^^^^^^^^^^
AssertionError
```

The problem is in `bazel_tools.get_important_outputs`; No `event` in `events` has `completed.important_output` field:
```
(Pdb) [e for e in events if e.id.HasField('target_completed')]
[id {
  target_completed {
    label: "//:arg_printer"
    configuration {
      id: "3723722276aac1314320ca9a9fb2a278d80e4eb3b422da8939e984231a537159"
    }
  }
}
completed {
  success: true
  output_group {
    name: "default"
    file_sets {
      id: "0"
    }
  }
}
]
(Pdb) [e.completed.important_output for e in events if e.id.HasField('target_completed')]
[[]]
(Pdb) [label_to_output[label] for label in labels]
[[]]
```

The `event`'s `named_set`, or more specifically `named_set_of_files`, seems to contain the needed information:
```
(Pdb) [e for e in events if e.id.HasField('named_set')]
[id {
  named_set {
    id: "0"
  }
}
named_set_of_files {
  files {
    name: "arg_printer"
    uri: "file:///home/user/.cache/bazel/_bazel_user/b2b20ce9d34fd5389bf343c4cc37f48f/execroot/_main/bazel-out/k8-fastbuild/bin/arg_printer"
    path_prefix: "bazel-out"
    path_prefix: "k8-fastbuild"
    path_prefix: "bin"
    digest: "49c3d23c74f2e92b4507289e5ea518eddecefa8128092421de28642adf934b6f"
    length: 14784
  }
}
]
```

The full `_get_important_outputs` would then become something like:
```
def _get_important_outputs(
    events: Sequence[bes_pb2.BuildEvent], labels: Sequence[str]
) -> List[List[bes_pb2.File]]:
  named_sets = {}
  for e in events:
    if e.id.HasField('named_set'):
      named_sets[e.id.named_set.id] = e.named_set_of_files

  label_to_output =  collections.defaultdict(list)
  for e in events:
    if e.id.HasField('target_completed'):
      label = e.id.target_completed.label

      # Start with whatever is in important_output (may be empty).
      outputs = list(e.completed.important_output)

      # Collect from output_group.file_sets references
      for group in e.completed.output_group:
        for fs_id in group.file_sets:
          queue = collections.deque([fs_id.id])
          visited = set()
          while queue:
            current = queue.popleft()
            if current in visited:
              continue
            visited.add(current)
            ns = named_sets.get(current)
            if ns:
              outputs.extend(ns.files)
              for nested in ns.file_sets:
                queue.append(nested.id)

      label_to_output[label] = outputs

  return [label_to_output[lbl] for lbl in labels]
```

This indeed makes the `local_arg_printer` example work:
```
$ python ./launcher.py
I0622 13:54:13.796687 130173373777728 migration.py:155] Context impl SQLiteImpl.
I0622 13:54:13.796775 130173373777728 migration.py:158] Will assume non-transactional DDL.
I0622 13:54:13.797553 130173373777728 migration.py:155] Context impl SQLiteImpl.
I0622 13:54:13.797580 130173373777728 migration.py:158] Will assume non-transactional DDL.
INFO: Analyzed target //:arg_printer (0 packages loaded, 0 targets configured).
INFO: Found 1 target...
Target //:arg_printer up-to-date:
  bazel-bin/arg_printer
INFO: Elapsed time: 0.098s, Critical Path: 0.00s
INFO: 1 process: 1 internal.
INFO: Build completed successfully, 1 total action
INFO: Build Event Protocol files produced successfully.
Waiting for local jobs to complete. Press Ctrl+C to terminate them and exit
Terminating 1 local job(s) that may still be running...
W0622 13:54:18.988335 130173373777728 execution.py:184] Unable to terminate BinaryHandle(name='_arg_printer', process=<Process 411874>, stream_output=True)
$ cat /tmp/local_arg_printer.txt
1
/home/user/xmanager/examples/local_arg_printer/bazel-out/k8-fastbuild/bin/arg_printer
```

I'm not an expert in bazel, and it's possible that the implementation above is lacking some edge cases or is otherwise not as general as needs to be. But it's worked for me so far.
