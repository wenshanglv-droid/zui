"""Core capabilities (filled from M0 onwards).

Planned modules:
    instance / proc / pidfile        instance registry and process supervision
    launch_env                       child-process environment injection
    comfy_args / profile             dynamic ComfyUI CLI parsing, launch profiles
    env_runtime / repair / torch_stack   environment inspection, self-heal, torch stacks
    snapshot                         snapshot create / diff / restore
    node / registry                  custom node management
    asset / safetensors_meta / budget    model index, metadata, workflow budget
    logparse / metrics                run pipeline parsing, sampling
    doctor / accelerate               health checks, one-click acceleration
    diag_package                      support bundle export
"""
