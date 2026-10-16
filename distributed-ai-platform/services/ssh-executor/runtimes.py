"""Language/runtime registry for agentless remote execution.

A :class:`Runtime` describes *how to run a program in one language* on a worker
that already has that language's toolchain. The platform **never installs
anything** on a worker: a runtime is offered for a node only when the detection
probe (see :mod:`capabilities`) found its tools there.

Roles in the whole execution path:

* ``capabilities.detect_capabilities`` imports this registry and builds one
  shell/PowerShell script that probes every runtime's tools in a single round
  trip, producing ``{runtime_id: version}`` for the node.
* ``execution_manager`` / ``routes_batches`` use that map to decide which nodes
  can serve a request.
* ``task_executor`` calls :func:`build_run_command` to produce the exact command
  it runs inside the task's throwaway sandbox.

Everything here is pure data + string building (no I/O), so it is unit-testable
offline and reusable by the adapters.

Command contract: every *run* command is executed with the task's stdin attached
and with the sandbox as the working directory / ``HOME`` / ``TMPDIR``. So a
command may reference ``./<filename>`` (relative, inside the sandbox) and any
build caches it creates land in the sandbox and are deleted with it.
``{file}`` is the placeholder for the source filename in run commands and in a
user-supplied ``custom`` command.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

#: Placeholder replaced with the source filename in run commands.
FILE_PLACEHOLDER = "{file}"

#: ``custom`` lets the user run any toolchain with their own command template.
CUSTOM_RUNTIME = "custom"

#: Filenames written into a sandbox must be a plain, safe basename.
_FILENAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


@dataclass(frozen=True)
class Runtime:
    """One language and the commands that run it.

    ``tool_names`` are the executables probed for availability (POSIX names,
    resolved through the node's login-shell ``PATH``). ``version_command``
    prints a version and is used only for display. ``run_command`` /
    ``windows_run_command`` are the launch commands; they may contain
    ``{file}``. ``kind`` distinguishes interpreted languages from ones that
    compile first (their build output and caches stay inside the sandbox).
    """

    id: str
    name: str
    filename: str
    kind: str  # interpreted | compiled | custom
    example_code: str
    tool_names: tuple[str, ...] = ()
    version_command: Optional[str] = None
    run_command: Optional[str] = None
    windows_run_command: Optional[str] = None
    windows_filename: Optional[str] = None
    #: Env vars that must point inside the sandbox (build/tool caches).
    sandbox_env: dict[str, str] = field(default_factory=dict)
    recommended_timeout_seconds: int = 120

    @property
    def windows_supported(self) -> bool:
        return bool(self.windows_run_command)

    def source_filename(self, os_type: str) -> str:
        if os_type == "windows" and self.windows_filename:
            return self.windows_filename
        return self.filename

    def command_for(self, os_type: str, custom_command: Optional[str] = None) -> str:
        """The launch command for this runtime on ``os_type``.

        Raises ``ValueError`` when the runtime has no command for that OS (e.g.
        most compiled toolchains on Windows) or when ``custom`` is missing its
        template.
        """
        if self.id == CUSTOM_RUNTIME:
            return validate_custom(custom_command, None)
        command = self.windows_run_command if os_type == "windows" else self.run_command
        if not command:
            raise ValueError(
                f"Runtime '{self.id}' is not supported on {os_type}. "
                f"Pick another language or use the 'custom' runtime with your own command."
            )
        return command


# --- the registry ---------------------------------------------------------
# NOTE: `example_code` values are shown in the UI and are run as-is, so each one
# reads a line from stdin and prints a result. Keep them dependency-free: only
# the language's own standard library may be used, because nothing is installed
# on the worker.

def _py(name: str, tool: str, run: str, example: str, **kw) -> Runtime:
    return Runtime(
        id=name, name=name.replace("_", " ").title(), filename=f"task.py",
        kind="interpreted", example_code=example, tool_names=(tool,),
        version_command=None, run_command=run, **kw,
    )


RUNTIMES: tuple[Runtime, ...] = (
    Runtime(
        id="python", name="Python", filename="task.py", kind="interpreted",
        tool_names=("python3", "python"), version_command="{tool} --version",
        run_command="{tool} -I -B task.py",
        windows_run_command="python -I -B task.py", windows_filename="task.py",
        sandbox_env={"PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1"},
        example_code=(
            "import sys\n"
            "data = sys.stdin.read().strip()\n"
            "print(f'python processed {len(data)} chars: {data[:40]}')\n"
        ),
    ),
    Runtime(
        id="bash", name="Bash", filename="task.sh", kind="interpreted",
        tool_names=("bash",), version_command="bash --version",
        run_command="bash task.sh",
        example_code=(
            "#!/usr/bin/env bash\n"
            "input=$(cat)\n"
            "echo \"bash processed ${#input} chars: ${input:0:40}\"\n"
        ),
    ),
    Runtime(
        id="sh", name="POSIX Shell", filename="task.sh", kind="interpreted",
        tool_names=("sh",), version_command="sh -c 'echo shell'",
        run_command="sh task.sh",
        example_code=(
            "#!/bin/sh\n"
            "input=$(cat)\n"
            "echo \"sh processed ${#input} chars\"\n"
        ),
    ),
    Runtime(
        id="powershell", name="PowerShell", filename="task.ps1", kind="interpreted",
        tool_names=("pwsh", "powershell"), version_command="{tool} -Version",
        run_command="pwsh -NoProfile -File task.ps1",
        windows_run_command="powershell -NoProfile -NonInteractive -File task.ps1",
        example_code=(
            "$input | ForEach-Object { \"powershell read: $_\" }\n"
        ),
    ),
    Runtime(
        id="node", name="Node.js (JavaScript)", filename="task.js", kind="interpreted",
        tool_names=("node", "nodejs"), version_command="node --version",
        run_command="node task.js",
        windows_run_command="node task.js",
        sandbox_env={"npm_config_cache": "{sandbox}/.npm"},
        example_code=(
            "let data = '';\n"
            "process.stdin.on('data', (c) => { data += c; });\n"
            "process.stdin.on('end', () => {\n"
            "  console.log(`node processed ${data.trim().length} chars`);\n"
            "});\n"
        ),
    ),
    Runtime(
        id="deno", name="Deno (TypeScript/JavaScript)", filename="task.ts", kind="interpreted",
        tool_names=("deno",), version_command="deno --version",
        run_command="deno run --allow-read --allow-env task.ts",
        sandbox_env={"DENO_DIR": "{sandbox}/.deno"},
        example_code=(
            "const decoder = new TextDecoder();\n"
            "let data = '';\n"
            "for await (const chunk of Deno.stdin.readable) {\n"
            "  data += decoder.decode(chunk);\n"
            "}\n"
            "console.log(`deno processed ${data.trim().length} chars`);\n"
        ),
    ),
    Runtime(
        id="bun", name="Bun (JavaScript/TypeScript)", filename="task.ts", kind="interpreted",
        tool_names=("bun",), version_command="bun --version",
        run_command="bun run task.ts",
        sandbox_env={"BUN_INSTALL_CACHE_DIR": "{sandbox}/.bun"},
        example_code=(
            "const data = await new Response(Bun.stdin.stream()).text();\n"
            "console.log(`bun processed ${data.trim().length} chars`);\n"
        ),
    ),
    Runtime(
        id="ruby", name="Ruby", filename="task.rb", kind="interpreted",
        tool_names=("ruby",), version_command="ruby --version",
        run_command="ruby task.rb",
        example_code=(
            "data = STDIN.read.strip\n"
            "puts \"ruby processed #{data.length} chars\"\n"
        ),
    ),
    Runtime(
        id="perl", name="Perl", filename="task.pl", kind="interpreted",
        tool_names=("perl",), version_command="perl --version",
        run_command="perl task.pl",
        example_code=(
            "local $/; my $data = <STDIN>;\n"
            "print \"perl processed \" . length($data) . \" chars\\n\";\n"
        ),
    ),
    Runtime(
        id="php", name="PHP", filename="task.php", kind="interpreted",
        tool_names=("php",), version_command="php --version",
        run_command="php task.php",
        example_code=(
            "<?php\n"
            "$data = trim(stream_get_contents(STDIN));\n"
            "echo 'php processed ' . strlen($data) . \" chars\\n\";\n"
        ),
    ),
    Runtime(
        id="lua", name="Lua", filename="task.lua", kind="interpreted",
        tool_names=("lua", "lua5.4", "lua5.3"), version_command="lua -v",
        run_command="lua task.lua",
        example_code=(
            "local data = io.read('*a') or ''\n"
            "print('lua processed ' .. #data .. ' chars')\n"
        ),
    ),
    Runtime(
        id="r", name="R", filename="task.R", kind="interpreted",
        tool_names=("Rscript", "R"), version_command="Rscript --version",
        run_command="Rscript task.R",
        example_code=(
            "data <- paste(readLines(file('stdin')), collapse=' ')\n"
            "cat('R processed', nchar(data), 'chars\\n')\n"
        ),
    ),
    Runtime(
        id="julia", name="Julia", filename="task.jl", kind="interpreted",
        tool_names=("julia",), version_command="julia --version",
        run_command="julia task.jl",
        sandbox_env={"JULIA_DEPOT_PATH": "{sandbox}/.julia"},
        example_code=(
            "data = strip(read(stdin, String))\n"
            "println(\"julia processed $(length(data)) chars\")\n"
        ),
    ),
    Runtime(
        id="go", name="Go", filename="main.go", kind="compiled",
        tool_names=("go",), version_command="go version",
        run_command="go run main.go",
        windows_run_command="go run main.go",
        sandbox_env={"GOCACHE": "{sandbox}/.gocache", "GOPATH": "{sandbox}/.gopath",
                     "GOFLAGS": "-mod=mod", "GOTOOLCHAIN": "local"},
        example_code=(
            "package main\n\n"
            "import (\n\t\"bufio\"\n\t\"fmt\"\n\t\"io\"\n\t\"os\"\n)\n\n"
            "func main() {\n"
            "\tdata, _ := io.ReadAll(bufio.NewReader(os.Stdin))\n"
            "\tfmt.Printf(\"go processed %d chars\\n\", len(data))\n"
            "}\n"
        ),
    ),
    Runtime(
        id="java", name="Java (single file, JDK 11+)", filename="Task.java", kind="compiled",
        tool_names=("java",), version_command="java -version",
        run_command="java Task.java",
        windows_run_command="java Task.java",
        example_code=(
            "import java.util.Scanner;\n"
            "public class Task {\n"
            "    public static void main(String[] args) {\n"
            "        Scanner s = new Scanner(System.in);\n"
            "        String data = s.hasNextLine() ? s.nextLine() : \"\";\n"
            "        System.out.println(\"java processed \" + data.length() + \" chars\");\n"
            "    }\n"
            "}\n"
        ),
    ),
    Runtime(
        id="kotlin", name="Kotlin (script)", filename="task.kts", kind="interpreted",
        tool_names=("kotlinc",), version_command="kotlinc -version",
        run_command="kotlinc -script task.kts",
        example_code=(
            "val data = generateSequence(::readLine).joinToString(\"\\n\").trim()\n"
            "println(\"kotlin processed ${data.length} chars\")\n"
        ),
    ),
    Runtime(
        id="scala", name="Scala (script)", filename="task.scala", kind="interpreted",
        tool_names=("scala", "scala-cli"), version_command="scala -version",
        run_command="scala task.scala",
        sandbox_env={"COURSIER_CACHE": "{sandbox}/.coursier"},
        example_code=(
            "val data = scala.io.Source.stdin.getLines().mkString(\"\\n\").trim\n"
            "println(s\"scala processed ${data.length} chars\")\n"
        ),
    ),
    Runtime(
        id="c", name="C", filename="main.c", kind="compiled",
        tool_names=("cc", "gcc", "clang"), version_command="{tool} --version",
        run_command="{tool} -O2 -o ./prog main.c && ./prog",
        sandbox_env={"TMPDIR": "{sandbox}"},
        example_code=(
            "#include <stdio.h>\n"
            "int main(void) {\n"
            "    long n = 0; int c;\n"
            "    while ((c = getchar()) != EOF) n++;\n"
            "    printf(\"c processed %ld chars\\n\", n);\n"
            "    return 0;\n"
            "}\n"
        ),
    ),
    Runtime(
        id="cpp", name="C++", filename="main.cpp", kind="compiled",
        tool_names=("c++", "g++", "clang++"), version_command="{tool} --version",
        run_command="{tool} -O2 -std=c++17 -o ./prog main.cpp && ./prog",
        sandbox_env={"TMPDIR": "{sandbox}"},
        example_code=(
            "#include <iostream>\n"
            "#include <string>\n"
            "int main() {\n"
            "    std::string data((std::istreambuf_iterator<char>(std::cin)),\n"
            "                     std::istreambuf_iterator<char>());\n"
            "    std::cout << \"cpp processed \" << data.size() << \" chars\\n\";\n"
            "    return 0;\n"
            "}\n"
        ),
    ),
    Runtime(
        id="rust", name="Rust", filename="main.rs", kind="compiled",
        tool_names=("rustc",), version_command="rustc --version",
        run_command="rustc -O -o ./prog main.rs && ./prog",
        sandbox_env={"CARGO_HOME": "{sandbox}/.cargo", "TMPDIR": "{sandbox}"},
        example_code=(
            "use std::io::Read;\n"
            "fn main() {\n"
            "    let mut data = String::new();\n"
            "    std::io::stdin().read_to_string(&mut data).unwrap();\n"
            "    println!(\"rust processed {} chars\", data.trim().len());\n"
            "}\n"
        ),
    ),
    Runtime(
        id="swift", name="Swift (script)", filename="task.swift", kind="interpreted",
        tool_names=("swift",), version_command="swift --version",
        run_command="swift task.swift",
        sandbox_env={"SWIFT_MODULECACHE_PATH": "{sandbox}/.swift-cache"},
        example_code=(
            "var data = \"\"\n"
            "while let line = readLine() { data += line }\n"
            "print(\"swift processed \\(data.count) chars\")\n"
        ),
    ),
    Runtime(
        id="dart", name="Dart", filename="task.dart", kind="interpreted",
        tool_names=("dart",), version_command="dart --version",
        run_command="dart run task.dart",
        sandbox_env={"PUB_CACHE": "{sandbox}/.pub-cache"},
        example_code=(
            "import 'dart:convert';\n"
            "import 'dart:io';\n"
            "Future<void> main() async {\n"
            "  final data = await utf8.decoder.bind(stdin).join();\n"
            "  print('dart processed ${data.trim().length} chars');\n"
            "}\n"
        ),
    ),
    Runtime(
        id="haskell", name="Haskell (runghc)", filename="task.hs", kind="interpreted",
        tool_names=("runghc", "runhaskell"), version_command="{tool} --version",
        run_command="{tool} task.hs",
        example_code=(
            "main :: IO ()\n"
            "main = do\n"
            "  data <- getContents\n"
            "  putStrLn (\"haskell processed \" ++ show (length data) ++ \" chars\")\n"
        ),
    ),
    Runtime(
        id="csharp", name="C# (dotnet-script)", filename="task.csx", kind="interpreted",
        tool_names=("dotnet-script", "dotnet"), version_command="{tool} --version",
        run_command="{tool} task.csx",
        sandbox_env={"DOTNET_CLI_HOME": "{sandbox}", "NUGET_PACKAGES": "{sandbox}/.nuget"},
        example_code=(
            "using System;\n"
            "var data = Console.In.ReadToEnd().Trim();\n"
            "Console.WriteLine($\"csharp processed {data.Length} chars\");\n"
        ),
    ),
    Runtime(
        id="elixir", name="Elixir (script)", filename="task.exs", kind="interpreted",
        tool_names=("elixir",), version_command="elixir --version",
        run_command="elixir task.exs",
        sandbox_env={"MIX_HOME": "{sandbox}/.mix", "HEX_HOME": "{sandbox}/.hex"},
        example_code=(
            "data = IO.read(:stdio, :eof) |> to_string() |> String.trim()\n"
            "IO.puts(\"elixir processed #{String.length(data)} chars\")\n"
        ),
    ),
    Runtime(
        id="erlang", name="Erlang (escript)", filename="task", kind="interpreted",
        tool_names=("escript",), version_command="escript --version",
        run_command="escript task",
        example_code=(
            "#!/usr/bin/env escript\n"
            "main(_) ->\n"
            "    {ok, Data} = file:read(standard_io),\n"
            "    io:format(\"erlang processed ~p chars~n\", [string:length(string:trim(Data))]).\n"
        ),
    ),
    Runtime(
        id="ocaml", name="OCaml (script)", filename="task.ml", kind="interpreted",
        tool_names=("ocaml",), version_command="ocaml -version",
        run_command="ocaml task.ml",
        sandbox_env={"OCAMLPATH": "{sandbox}/.ocaml"},
        example_code=(
            "let () =\n"
            "  let data = In_channel.input_all In_channel.stdin in\n"
            "  Printf.printf \"ocaml processed %d chars\\n\" (String.length (String.trim data))\n"
        ),
    ),
    Runtime(
        id="tcl", name="Tcl", filename="task.tcl", kind="interpreted",
        tool_names=("tclsh",), version_command="echo 'puts $tcl_version' | tclsh",
        run_command="tclsh task.tcl",
        example_code=(
            "set data [string trim [read stdin]]\n"
            "puts \"tcl processed [string length $data] chars\"\n"
        ),
    ),
    Runtime(
        id="awk", name="AWK", filename="task.awk", kind="interpreted",
        tool_names=("awk", "gawk", "mawk"), version_command="{tool} --version",
        run_command="{tool} -f task.awk",
        example_code=(
            "{ n += length($0) }\n"
            "END { printf(\"awk processed %d chars\\n\", n) }\n"
        ),
    ),
    Runtime(
        id=CUSTOM_RUNTIME, name="Custom command", filename="task.txt", kind="custom",
        tool_names=(), run_command=None,
        example_code=(
            "# Write any source file here and give the exact command that runs it.\n"
        ),
    ),
)

_BY_ID: dict[str, Runtime] = {r.id: r for r in RUNTIMES}

#: Runtime ids a node is expected to have for the platform's own training path.
CORE_RUNTIME_ID = "python"


# --- registry access ------------------------------------------------------
def list_runtimes() -> list[Runtime]:
    """Every registered runtime (``custom`` last)."""
    return [r for r in RUNTIMES if r.id != CUSTOM_RUNTIME] + [_BY_ID[CUSTOM_RUNTIME]]


def get_runtime(runtime_id: str) -> Optional[Runtime]:
    """The runtime with this id, or ``None`` if it is not registered."""
    if not runtime_id:
        return None
    return _BY_ID.get(str(runtime_id).strip().lower())


def runtime_ids() -> list[str]:
    return [r.id for r in list_runtimes()]


# --- custom command validation --------------------------------------------
def validate_custom(
    custom_command: Optional[str], custom_filename: Optional[str] = None
) -> str:
    """Validate a ``custom`` run command; return it normalized.

    The command must contain the ``{file}`` placeholder (so the user says where
    their source file goes) and must be a single line. When ``custom_filename``
    is given it must be a safe basename. Raises ``ValueError`` otherwise.
    """
    if not custom_command or not str(custom_command).strip():
        raise ValueError(
            f"Runtime '{CUSTOM_RUNTIME}' needs a run command containing {FILE_PLACEHOLDER} "
            "(for example: 'go run {file}' or 'zig run {file}')"
        )
    command = str(custom_command).strip()
    if FILE_PLACEHOLDER not in command:
        raise ValueError(
            f"The custom run command must contain the {FILE_PLACEHOLDER} placeholder, "
            "which is replaced with the filename of your source code."
        )
    if "\n" in command or "\r" in command or "\x00" in command:
        raise ValueError("The custom run command must be a single line")
    if custom_filename is not None:
        validate_filename(custom_filename)
    return command


def validate_filename(filename: Optional[str]) -> str:
    """Validate a user-supplied source filename (a safe basename)."""
    name = (filename or "").strip()
    if not _FILENAME_RE.match(name):
        raise ValueError(
            "The filename must be 1-128 characters of letters, digits, dot, underscore or dash"
        )
    if name in (".", "..") or name.startswith("-"):
        raise ValueError("The filename must be a plain file name")
    return name


def build_run_command(
    runtime: Runtime | str,
    filename: Optional[str] = None,
    os_type: str = "linux",
    custom_command: Optional[str] = None,
) -> str:
    """Build the shell command that runs ``runtime`` inside a sandbox.

    ``filename`` defaults to the runtime's own source filename for the OS. The
    returned command is POSIX/probe-agnostic: the caller runs it through the
    OS's shell with the sandbox as the working directory, and the
    ``{tool}``/``{file}`` placeholders are substituted first.
    """
    rt = get_runtime(runtime) if isinstance(runtime, str) else runtime
    if rt is None:
        raise ValueError(f"Unknown runtime '{runtime}'")

    if rt.id == CUSTOM_RUNTIME:
        command = validate_custom(custom_command, filename)
        target = validate_filename(filename) if filename else rt.source_filename(os_type)
        return command.replace(FILE_PLACEHOLDER, target)

    target = validate_filename(filename) if filename else rt.source_filename(os_type)
    command = rt.command_for(os_type)
    return _substitute_tool(command, rt, os_type).replace(FILE_PLACEHOLDER, target)


def _substitute_tool(command: str, runtime: Runtime, os_type: str) -> str:
    """Replace ``{tool}`` with the first tool name (kept symbolic for shells).

    The registry lists alternatives (``cc``/``gcc``/``clang``); the probe
    already established that at least one exists, so the first name is used
    directly. Detection-time probing resolves the same order.
    """
    if "{tool}" not in command:
        return command
    tool = runtime.tool_names[0] if runtime.tool_names else ""
    return command.replace("{tool}", tool)


def detection_script(runtime_list: Optional[list[Runtime]] = None) -> str:
    """A POSIX shell script that prints ``id<TAB>version`` for present runtimes.

    One round trip detects every language. A runtime is reported only when one
    of its tool names resolves on the node's ``PATH``; the version string comes
    from ``version_command`` when defined, else the tool's resolved path. Tools
    that are missing are simply not printed.
    """
    lines = ["set -u"]
    for rt in (runtime_list or list_runtimes()):
        if rt.id == CUSTOM_RUNTIME or not rt.tool_names:
            continue
        candidates = " ".join(_sh_quote(t) for t in rt.tool_names)
        version = rt.version_command or "{tool} --help >/dev/null 2>&1 && echo available"
        # Resolve the first tool that exists, then print "<id>\t<version>".
        script = (
            f'for tool in {candidates}; do '
            f'if command -v "$tool" >/dev/null 2>&1; then '
            f'v=$({version}); '
            f'printf "%s\\t%s\\n" {_sh_quote(rt.id)} "$(printf "%s" "$v" | head -n 1)"; '
            f'break; fi; done'
        )
        lines.append(f"{{ {script} ; }} 2>/dev/null || true")
    return "\n".join(lines)


def windows_detection_script(runtime_list: Optional[list[Runtime]] = None) -> str:
    """A PowerShell script doing the same as :func:`detection_script` on Windows."""
    lines = []
    for rt in (runtime_list or list_runtimes()):
        if rt.id == CUSTOM_RUNTIME or not rt.tool_names:
            continue
        candidates = ", ".join(f"'{t}'" for t in rt.tool_names)
        lines.append(
            f"foreach ($tool in @({candidates})) {{ "
            f"$cmd = Get-Command $tool -ErrorAction SilentlyContinue; "
            f"if ($cmd) {{ "
            f"$v = ''; try {{ $v = (& $tool --version 2>&1 | Select-Object -First 1) }} catch {{}}; "
            f'Write-Output ("{rt.id}`t" + $v); break }} }}'
        )
    return "\n".join(lines)


def parse_detection_output(output: str) -> dict[str, str]:
    """Parse ``id<TAB>version`` lines into ``{runtime_id: version}``.

    Tolerant: unknown ids are dropped, whitespace trimmed, versions may be
    empty (the tool exists but printed nothing useful).
    """
    found: dict[str, str] = {}
    for raw in (output or "").splitlines():
        line = raw.strip()
        if not line or "\t" not in line:
            continue
        runtime_id, _, version = line.partition("\t")
        runtime_id = runtime_id.strip().lower()
        if runtime_id in _BY_ID and runtime_id != CUSTOM_RUNTIME:
            found[runtime_id] = version.strip()[:64]
    return found


def sandbox_env_for(runtime: Runtime | str, sandbox_dir: str) -> dict[str, str]:
    """Cache/tool environment variables for ``runtime`` pointing inside a sandbox."""
    rt = get_runtime(runtime) if isinstance(runtime, str) else runtime
    if rt is None:
        return {}
    return {
        key: value.replace("{sandbox}", sandbox_dir)
        for key, value in rt.sandbox_env.items()
    }


def _sh_quote(value: str) -> str:
    return "'" + str(value).replace("'", "'\\''") + "'"
