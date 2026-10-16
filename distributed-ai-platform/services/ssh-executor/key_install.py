"""Install / remove the platform's managed key in a node's authorized keys.

All helpers run over an already-connected :class:`ssh_transport.SSHTransport`.

POSIX (Linux, macOS)
    The remote command is just ``sh -s``; the whole script travels on **stdin**,
    so the user's login shell (bash, zsh, fish, csh, ...) never parses it. The
    key line is embedded in a quoted here-doc and all other values are
    ``shlex.quote``-d and validated against a strict character set first.
    Install is idempotent (a line with the same comment *and* key is not added
    twice), runs under ``umask 077`` and fixes ``~/.ssh`` (700) and
    ``authorized_keys`` (600). Removal writes a filtered copy (``cp -p`` keeps
    the mode) and ``mv``-s it into place.

Windows (OpenSSH for Windows)
    ``powershell -NoProfile -NonInteractive -EncodedCommand <base64 UTF-16LE>``
    so cmd.exe / PowerShell quoting can never break the script. Members of the
    local Administrators group are authenticated by the default ``sshd_config``
    against ``%ProgramData%\\ssh\\administrators_authorized_keys``, which must be
    ACL'd to Administrators + SYSTEM only (``icacls`` with well-known SIDs, so
    it also works on localized Windows). Everyone else uses
    ``%USERPROFILE%\\.ssh\\authorized_keys``. NOTE: verified with mocks only, not
    yet on a real Windows host.

Every script prints one ``DPKEY OK ...`` marker line on success.
"""
from __future__ import annotations

import base64
import re
import shlex
from typing import Optional, Tuple

from key_manager import validate_comment

_MARKER = "DPKEY OK"
_LINE_RE = re.compile(r'^[A-Za-z0-9 ,=".:/@_*+-]{16,4096}$')
_BLOB_RE = re.compile(r"^[A-Za-z0-9+/]*={0,3}$")
_HEREDOC_TAG = "__DP_PLATFORM_KEY_LINE__"


class KeyInstallError(RuntimeError):
    """The remote install/remove script failed."""


# --- validation ----------------------------------------------------------------
def _check_line(line: str, comment: str) -> str:
    if not _LINE_RE.match(line or "") or "\n" in line or "\r" in line:
        raise ValueError("Refusing to install a malformed authorized_keys line")
    if line.split()[-1] != comment:
        raise ValueError("authorized_keys line must end with its comment")
    return line


def _check_blob(blob: Optional[str]) -> str:
    blob = blob or ""
    if not _BLOB_RE.match(blob):
        raise ValueError("Invalid public key blob")
    return blob


def key_blob_of(line: str) -> str:
    """The base64 key blob of an authorized_keys line (the field after the type)."""
    parts = line.split()
    for i, part in enumerate(parts[:-1]):
        if part.startswith(("ssh-", "ecdsa-")):
            return parts[i + 1]
    raise ValueError("No key type found in authorized_keys line")


# --- POSIX scripts ----------------------------------------------------------------
# Shared awk matcher: m == 1 when the last field is the comment and (if a key
# blob is given) one of the other fields is exactly that blob.
_AWK_MATCH = (
    '{ m = ($NF == c); if (m && k != "") { m = 0; '
    "for (i = 1; i < NF; i++) if ($i == k) m = 1 } }"
)


def posix_install_script(line: str, comment: str) -> str:
    validate_comment(comment)
    _check_line(line, comment)
    blob = _check_blob(key_blob_of(line))
    return f"""set -u
umask 077
line=$(cat <<'{_HEREDOC_TAG}'
{line}
{_HEREDOC_TAG}
)
comment={shlex.quote(comment)}
blob={shlex.quote(blob)}
d="$HOME/.ssh"
f="$d/authorized_keys"
mkdir -p "$d" || exit 10
touch "$f" || exit 11
n=$(awk -v c="$comment" -v k="$blob" '{_AWK_MATCH} m == 1 {{ n++ }} END {{ print n + 0 }}' "$f") || exit 12
if [ "$n" -gt 0 ]; then
  state=present
else
  if [ -s "$f" ] && [ -n "$(tail -c 1 "$f")" ]; then printf '\\n' >> "$f" || exit 13; fi
  printf '%s\\n' "$line" >> "$f" || exit 13
  state=added
fi
chmod 700 "$d" || exit 14
chmod 600 "$f" || exit 14
if command -v restorecon >/dev/null 2>&1; then restorecon -R "$d" >/dev/null 2>&1 || true; fi
echo "{_MARKER} state=$state path=$f"
"""


def posix_remove_script(comment: str, key_blob: Optional[str] = None) -> str:
    validate_comment(comment)
    blob = _check_blob(key_blob)
    return f"""set -u
umask 077
comment={shlex.quote(comment)}
blob={shlex.quote(blob)}
f="$HOME/.ssh/authorized_keys"
if [ ! -f "$f" ]; then echo "{_MARKER} removed=0 path=$f"; exit 0; fi
n=$(awk -v c="$comment" -v k="$blob" '{_AWK_MATCH} m == 1 {{ n++ }} END {{ print n + 0 }}' "$f") || exit 20
if [ "$n" -gt 0 ]; then
  tmp="$f.dp-tmp.$$"
  cp -p "$f" "$tmp" || exit 21
  if ! awk -v c="$comment" -v k="$blob" '{_AWK_MATCH} m == 0 {{ print }}' "$f" > "$tmp"; then rm -f "$tmp"; exit 22; fi
  if [ -L "$f" ]; then
    cat "$tmp" > "$f" || {{ rm -f "$tmp"; exit 23; }}
    rm -f "$tmp"
  else
    mv -f "$tmp" "$f" || {{ rm -f "$tmp"; exit 23; }}
  fi
fi
echo "{_MARKER} removed=$n path=$f"
"""


# --- Windows scripts (PowerShell 5.1+) ------------------------------------------
def _ps_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


_PS_TARGET = r"""
$isAdmin = $false
try {
  $principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
  $isAdmin = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
} catch {}
if (-not $isAdmin) {
  try { if ((whoami.exe /groups) -match 'S-1-5-32-544') { $isAdmin = $true } } catch {}
}
$useAdminFile = $false
if ($isAdmin) {
  $useAdminFile = $true
  $cfg = Join-Path $env:ProgramData 'ssh\sshd_config'
  if (Test-Path -LiteralPath $cfg) {
    $useAdminFile = [bool](Select-String -LiteralPath $cfg -Pattern '^\s*AuthorizedKeysFile\s+.*administrators_authorized_keys' -Quiet)
  }
}
if ($useAdminFile) {
  $dir = Join-Path $env:ProgramData 'ssh'
  $file = Join-Path $dir 'administrators_authorized_keys'
} else {
  $dir = Join-Path $env:USERPROFILE '.ssh'
  $file = Join-Path $dir 'authorized_keys'
}
"""

_PS_MATCH_FN = r"""
function Test-DpLine([string]$l) {
  $parts = @($l.Trim() -split '\s+')
  if ($parts.Count -lt 2 -or -not ($parts[-1] -ceq $comment)) { return $false }
  if ($blob -ne '') { return ($parts -ccontains $blob) }
  return $true
}
"""


def windows_install_script(line: str, comment: str) -> str:
    validate_comment(comment)
    _check_line(line, comment)
    blob = _check_blob(key_blob_of(line))
    return (
        "$ErrorActionPreference = 'Stop'\n"
        f"$line = {_ps_literal(line)}\n"
        f"$comment = {_ps_literal(comment)}\n"
        f"$blob = {_ps_literal(blob)}\n"
        + _PS_MATCH_FN
        + _PS_TARGET
        + r"""
if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
$text = ''
if (Test-Path -LiteralPath $file) { $text = [IO.File]::ReadAllText($file) }
$present = $false
foreach ($l in ($text -split "`r?`n")) { if (Test-DpLine $l) { $present = $true } }
$state = 'present'
if (-not $present) {
  $prefix = ''
  if ($text.Length -gt 0 -and -not $text.EndsWith("`n")) { $prefix = "`r`n" }
  [IO.File]::AppendAllText($file, $prefix + $line + "`r`n", (New-Object Text.UTF8Encoding($false)))
  $state = 'added'
}
if ($useAdminFile) {
  & icacls.exe $file /inheritance:r /grant '*S-1-5-32-544:F' /grant '*S-1-5-18:F' | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "icacls failed with exit code $LASTEXITCODE" }
}
Write-Output "DPKEY OK state=$state path=$file"
"""
    )


def windows_remove_script(comment: str, key_blob: Optional[str] = None) -> str:
    validate_comment(comment)
    blob = _check_blob(key_blob)
    return (
        "$ErrorActionPreference = 'Stop'\n"
        f"$comment = {_ps_literal(comment)}\n"
        f"$blob = {_ps_literal(blob)}\n"
        + _PS_MATCH_FN
        + r"""
$total = 0
$candidates = @((Join-Path $env:ProgramData 'ssh\administrators_authorized_keys'), (Join-Path $env:USERPROFILE '.ssh\authorized_keys'))
foreach ($file in $candidates) {
  try {
    if (-not (Test-Path -LiteralPath $file)) { continue }
    $keep = New-Object System.Collections.Generic.List[string]
    $n = 0
    foreach ($l in [IO.File]::ReadAllLines($file)) { if (Test-DpLine $l) { $n++ } else { $keep.Add($l) } }
    if ($n -gt 0) {
      $body = ($keep -join "`r`n")
      if ($keep.Count -gt 0) { $body += "`r`n" }
      # Writing into the existing file keeps its ACL.
      [IO.File]::WriteAllText($file, $body, (New-Object Text.UTF8Encoding($false)))
      $total += $n
    }
  } catch [System.UnauthorizedAccessException] {}
}
Write-Output "DPKEY OK removed=$total"
"""
    )


def powershell_encoded_command(script: str) -> str:
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return f"powershell -NoProfile -NonInteractive -EncodedCommand {encoded}"


# --- running over a transport -----------------------------------------------------
def _run(transport, os_type: str, script: str, timeout: int) -> str:
    if (os_type or "").lower() == "windows":
        res = transport.exec(powershell_encoded_command(script), timeout=timeout)
    else:
        res = transport.exec("sh -s", input_data=script, timeout=timeout)
    stdout = res.get("stdout") or ""
    marker = next((l.strip() for l in stdout.splitlines() if l.strip().startswith(_MARKER)), None)
    if res.get("exit_code") != 0 or marker is None:
        err = (res.get("stderr") or "").strip()[-500:]
        raise KeyInstallError(
            f"authorized_keys update failed (exit {res.get('exit_code')}): {err or stdout.strip()[-500:]}"
        )
    return marker


def _is_windows(os_type: str) -> bool:
    return (os_type or "").lower() == "windows"


def _field(marker: str, name: str) -> Optional[str]:
    m = re.search(rf"\b{name}=(\S.*?)(?=\s+\w+=|$)", marker)
    return m.group(1) if m else None


def install_authorized_key(transport, os_type: str, line: str, comment: str, timeout: int = 30) -> dict:
    """Append ``line`` to the right authorized keys file (idempotent).

    Returns ``{"state": "added"|"present", "path": <remote file>}``.
    """
    script = (windows_install_script if _is_windows(os_type) else posix_install_script)(line, comment)
    marker = _run(transport, os_type, script, timeout)
    return {"state": _field(marker, "state"), "path": _field(marker, "path")}


def remove_authorized_key(
    transport, os_type: str, comment: str, key_blob: Optional[str] = None, timeout: int = 30
) -> int:
    """Remove our line(s) by comment (and key blob when given). Returns count."""
    script = (windows_remove_script if _is_windows(os_type) else posix_remove_script)(comment, key_blob)
    marker = _run(transport, os_type, script, timeout)
    try:
        return int(_field(marker, "removed") or 0)
    except ValueError:
        return 0


# --- manual fallback (password login disabled on the node) -----------------------
def manual_install_commands(line: str, comment: str) -> Tuple[str, str]:
    """Return ``(posix_one_liner, powershell_script)`` the user can paste on
    the worker themselves. Both are idempotent and safe to run twice."""
    validate_comment(comment)
    _check_line(line, comment)
    posix_body = (
        'umask 077; mkdir -p "$HOME/.ssh" && touch "$HOME/.ssh/authorized_keys"'
        ' && chmod 700 "$HOME/.ssh" && chmod 600 "$HOME/.ssh/authorized_keys"'
        ' && { grep -qF -- "$2" "$HOME/.ssh/authorized_keys"'
        ' || printf "\\n%s\\n" "$1" >> "$HOME/.ssh/authorized_keys"; }'
    )
    posix = f"sh -c {shlex.quote(posix_body)} sh {shlex.quote(line)} {shlex.quote(comment)}"
    windows = (
        "# Run in PowerShell as the SSH user (elevated if that user is an administrator)\n"
        + windows_install_script(line, comment)
    )
    return posix, windows
