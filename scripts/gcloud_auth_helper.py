import re
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GCLOUD = Path.home() / "AppData" / "Local" / "Google" / "Cloud SDK" / "google-cloud-sdk" / "bin" / "gcloud.cmd"
URL_FILE = ROOT / "gcloud-auth-url.txt"
CODE_FILE = ROOT / "gcloud-auth-code.txt"
RESULT_FILE = ROOT / "gcloud-auth-result.txt"


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def main() -> int:
    for path in [URL_FILE, CODE_FILE, RESULT_FILE]:
        if path.exists():
            path.unlink()

    proc = subprocess.Popen(
        [str(GCLOUD), "auth", "login", "--no-launch-browser", "--brief"],
        cwd=str(ROOT),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    output = []
    auth_url = None
    started = time.time()
    while time.time() - started < 60:
        chunk = proc.stdout.read(1)
        if chunk:
            output.append(chunk)
            text = "".join(output)
            match = re.search(r"https://accounts\.google\.com/\S+", text)
            if match:
                auth_url = match.group(0)
                write(URL_FILE, auth_url)
                break
        elif proc.poll() is not None:
            break

    if not auth_url:
        write(RESULT_FILE, "AUTH_URL_NOT_FOUND\n" + "".join(output))
        return 1

    deadline = time.time() + 900
    while time.time() < deadline:
        if CODE_FILE.exists():
            code = CODE_FILE.read_text(encoding="utf-8").strip()
            if code:
                proc.stdin.write(code + "\n")
                proc.stdin.flush()
                break
        if proc.poll() is not None:
            break
        time.sleep(1)
    else:
        proc.kill()
        write(RESULT_FILE, "TIMEOUT_WAITING_FOR_CODE\n" + "".join(output))
        return 1

    try:
        rest, _ = proc.communicate(timeout=180)
    except subprocess.TimeoutExpired:
        proc.kill()
        rest, _ = proc.communicate()

    full = "".join(output) + (rest or "")
    write(RESULT_FILE, full)
    return proc.returncode or 0


if __name__ == "__main__":
    sys.exit(main())
