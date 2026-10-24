"""The one check command every acceptance change oracle declares.

The product oracle runs it inside a checkout of the repository under test.
Each acceptance case keeps its repository at ``<case>/repo`` and its oracle at
``<case>/harness``, so the launcher finds the check a scenario installed there.
The command is identical for every arm and replicate, as the oracle
configuration of a comparable pair must be. A case whose scenario installed no
check exits 127: the oracle never ran, which is an infrastructure failure.
"""

from pathlib import Path
import subprocess
import sys


def main() -> int:
    common = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
                            capture_output=True, text=True, check=True).stdout.strip()
    check = Path(common).parent.parent / "harness" / "acceptance_check.py"
    if not check.is_file():
        print(f"no acceptance check is installed at {check}", file=sys.stderr)
        return 127
    return subprocess.run([sys.executable, "-B", str(check)]).returncode


if __name__ == "__main__":
    sys.exit(main())
