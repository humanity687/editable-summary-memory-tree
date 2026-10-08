from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, Callable, List
from multiprocessing import Process, Queue
from concurrent.futures import ThreadPoolExecutor
import threading
import queue
import time
import os


# ── Worker process (runs in subprocess for isolation + GUI support) ──

def _worker_main(input_queue: Queue, output_queue: Queue):
    """Persistent subprocess worker. Maintains namespace across commands."""
    import sys as _sys
    import os as _os
    import io as _io
    import traceback as _traceback
    import builtins as _builtins
    from contextlib import redirect_stdout as _redirect_stdout
    from contextlib import redirect_stderr as _redirect_stderr

    globals_dict: Dict[str, Any] = {
        '__name__': '__console__',
        '__doc__': None,
        '__builtins__': _builtins,
    }
    globals_dict['sys'] = _sys
    globals_dict['os'] = _os

    while True:
        try:
            msg = input_queue.get()
            if msg is None:
                break

            msg_type = msg[0]

            if msg_type == 'exec':
                command_id = msg[1]
                command = msg[2].lstrip('\\n').lstrip()

                output = ""
                error = ""
                result_repr = None

                stdout_buf = _io.StringIO()
                stderr_buf = _io.StringIO()

                try:
                    with _redirect_stdout(stdout_buf), _redirect_stderr(stderr_buf):
                        try:
                            code_obj = compile(command, "<input>", "eval")
                            result = eval(code_obj, globals_dict)
                            if result is not None:
                                result_repr = repr(result)
                                print(repr(result))
                        except SyntaxError:
                            code_obj = compile(command, "<input>", "exec")
                            exec(code_obj, globals_dict)
                except SystemExit:
                    error = "错误：代码尝试退出系统，操作被禁止"
                except KeyboardInterrupt:
                    error = "KeyboardInterrupt"
                except Exception:
                    error = _traceback.format_exc()

                stdout_content = stdout_buf.getvalue()
                stderr_content = stderr_buf.getvalue()

                output = stdout_content
                if stderr_content:
                    output += stderr_content

                try:
                    output_queue.put(('exec_result', command_id, output, error, result_repr))
                except Exception:
                    break

            elif msg_type == 'get_variables':
                command_id = msg[1]
                include_private = msg[2]

                import pickle as _pickle
                variables = {}
                for name, value in list(globals_dict.items()):
                    if not include_private and name.startswith('_'):
                        continue
                    try:
                        _pickle.dumps(value)
                        variables[name] = value
                    except Exception:
                        try:
                            variables[name] = repr(value)
                        except Exception:
                            variables[name] = f"<{type(value).__name__} object>"

                try:
                    output_queue.put(('variables_result', command_id, variables))
                except Exception:
                    break

        except EOFError:
            break
        except Exception:
            try:
                output_queue.put(('error', -1, "Worker process internal error"))
            except Exception:
                break


# ── Abstract Base ──

class BaseAHIShell(ABC):
    """Shell abstract base class. All custom shells inherit from this."""

    def __init__(self, on_result_callback: Optional[Callable] = None):
        self.command_queue = queue.Queue()
        self.result_queue = queue.Queue()
        self.running = True
        self.on_result_callback = on_result_callback
        self._executor = None
        self.execution_thread = threading.Thread(target=self._execution_loop, daemon=True)
        self.execution_thread.start()

    def submit_command(self, command: str, exec_id: str = None,
                       timeout: float = None) -> str:
        if exec_id is None:
            exec_id = f"cmd_{hash(command)}_{int(time.time() * 1000)}"
        self.command_queue.put((exec_id, command, timeout))
        return exec_id

    def get_results(self) -> List[Dict[str, Any]]:
        results = []
        while not self.result_queue.empty():
            results.append(self.result_queue.get())
        return results

    def _execution_loop(self):
        while self.running:
            try:
                exec_id, command, timeout = self.command_queue.get(timeout=1)
                try:
                    result = self._execute_command_impl(command)
                    result_data = {
                        "exec_id": exec_id,
                        "command": command,
                        "result": result,
                        "status": "completed",
                    }
                except Exception as e:
                    result_data = {
                        "exec_id": exec_id,
                        "command": command,
                        "error": str(e),
                        "status": "error",
                    }
                self.result_queue.put(result_data)
                if self.on_result_callback:
                    try:
                        self.on_result_callback(result_data)
                    except Exception:
                        pass
            except queue.Empty:
                continue
            except Exception:
                pass

    @abstractmethod
    def _execute_command_impl(self, command: str) -> Any:
        """Execute a command and return result."""

    def reset(self) -> None:
        """Reset shell state."""

    @abstractmethod
    def get_state(self) -> Dict[str, Any]:
        """Return current shell state snapshot."""

    def shutdown(self):
        self.running = False


# ── PythonShell (subprocess-backed) ──

class PythonShell(BaseAHIShell):
    """Python shell using multiprocessing subprocess for GUI support and isolation.

    Maintains a persistent worker process that preserves namespace across commands.
    Uses ThreadPoolExecutor for timeout control.
    """

    def __init__(self, on_result_callback: Optional[Callable] = None,
                 config: Optional[Dict[str, Any]] = None):
        super().__init__(on_result_callback)
        self.config = config or {}
        self._input_queue: Optional[Queue] = None
        self._output_queue: Optional[Queue] = None
        self._process: Optional[Process] = None
        self._command_id: int = 0
        self._cid_lock = threading.Lock()
        self._pending: Dict[int, str] = {}  # cmd_id → exec_id

        self._executor = ThreadPoolExecutor(max_workers=2)

        self._start_worker()

    # ── Worker lifecycle ──

    def _start_worker(self):
        self._stop_worker()
        self._input_queue = Queue()
        self._output_queue = Queue()
        self._process = Process(
            target=_worker_main,
            args=(self._input_queue, self._output_queue),
        )
        self._process.daemon = True
        self._process.start()

    def _stop_worker(self):
        if self._input_queue:
            try:
                self._input_queue.put(None, timeout=1)
            except Exception:
                pass
        if self._process and self._process.is_alive():
            self._process.join(timeout=2)
            if self._process.is_alive():
                self._process.terminate()
                self._process.join(timeout=1)
        self._process = None
        self._input_queue = None
        self._output_queue = None

    def _ensure_worker(self):
        if self._process is None or not self._process.is_alive():
            self._start_worker()

    # ── Command submission (override with subprocess) ──

    def submit_command(self, command: str, exec_id: str = None,
                       timeout: float = 30) -> str:
        if exec_id is None:
            exec_id = f"cmd_{hash(command)}_{int(time.time() * 1000)}"

        self._ensure_worker()

        with self._cid_lock:
            self._command_id += 1
            cmd_id = self._command_id

        self._pending[cmd_id] = exec_id

        def _run(cid: int):
            try:
                self._input_queue.put(('exec', cid, command), timeout=5)
            except Exception as e:
                self.result_queue.put({
                    "exec_id": exec_id,
                    "command": command,
                    "error": f"无法发送命令到工作进程: {e}",
                    "status": "error",
                })
                self._start_worker()
                return

            deadline = time.time() + timeout
            while time.time() < deadline:
                try:
                    msg = self._output_queue.get(timeout=1)
                    msg_type = msg[0]
                    recv_id = msg[1]

                    if recv_id == cid:
                        if msg_type == 'exec_result':
                            output = msg[2]
                            error = msg[3]
                            result_repr = msg[4]
                            result_text = output
                            if error:
                                result_text = result_text + ("\n" + error if result_text else error)

                            result_data = {
                                "exec_id": exec_id,
                                "command": command,
                                "result": result_text,
                                "output": output,
                                "error": error,
                                "result_repr": result_repr,
                                "status": "error" if error else "completed",
                            }
                            self.result_queue.put(result_data)
                            if self.on_result_callback:
                                try:
                                    self.on_result_callback(result_data)
                                except Exception:
                                    pass
                            self._pending.pop(cid, None)
                            return
                        elif msg_type == 'error':
                            result_data = {
                                "exec_id": exec_id,
                                "command": command,
                                "error": str(msg[2]) if len(msg) > 2 else "Worker error",
                                "status": "error",
                            }
                            self.result_queue.put(result_data)
                            if self.on_result_callback:
                                try:
                                    self.on_result_callback(result_data)
                                except Exception:
                                    pass
                            self._pending.pop(cid, None)
                            return
                    # Discard stale messages
                except queue.Empty:
                    if not self._process or not self._process.is_alive():
                        self.result_queue.put({
                            "exec_id": exec_id,
                            "command": command,
                            "error": "错误：工作进程已崩溃",
                            "status": "error",
                        })
                        self._start_worker()
                        self._pending.pop(cid, None)
                        return

            # Timeout
            self.result_queue.put({
                "exec_id": exec_id,
                "command": command,
                "error": f"错误：命令执行超时（{timeout}秒）",
                "status": "timeout",
            })
            self._pending.pop(cid, None)
            # Restart worker on timeout (stuck command)
            self._start_worker()

        self._executor.submit(_run, cmd_id)
        return exec_id

    # ── Base class overrides ──

    def _execute_command_impl(self, command: str) -> str:
        """Not used directly — submit_command handles execution via subprocess."""
        return ""

    def reset(self) -> None:
        self._start_worker()
        with self._cid_lock:
            self._command_id = 0
            self._pending.clear()

    def get_state(self) -> Dict[str, Any]:
        worker_alive = self._process is not None and self._process.is_alive()
        pending_count = len(self._pending) if self._pending else 0
        return {
            "status": "running" if worker_alive else "restarting",
            "worker_alive": worker_alive,
            "pending_commands": pending_count,
        }

    def shutdown(self):
        super().shutdown()
        self._stop_worker()
        if self._executor:
            self._executor.shutdown(wait=False)
