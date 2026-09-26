Import("env")

import subprocess

PROJECT_DIR = env.subst("$PROJECT_DIR")


def git(args):
    try:
        return subprocess.check_output(
            ["git", "-C", PROJECT_DIR] + args, stderr=subprocess.DEVNULL
        ).decode().strip()
    except Exception:
        return "unknown"


firmware_version = git(["describe", "--tags", "--always", "--dirty"])
sdk_sha = git(["submodule", "status", "freeink-sdk"]).split()[0][:12]

env.Append(
    CPPDEFINES=[
        ("FIRMWARE_VERSION", '"%s"' % firmware_version),
        ("FREEINK_SDK_SHA", '"%s"' % sdk_sha),
    ]
)

print("build_version: FIRMWARE_VERSION=%s FREEINK_SDK_SHA=%s" % (firmware_version, sdk_sha))
