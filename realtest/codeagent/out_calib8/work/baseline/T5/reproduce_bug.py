import threading
import time
import queue
from typing import Any, Dict, Optional, Callable
from multiprocessing import Process, Queue
from concurrent.futures import ThreadPoolExecutor

def _worker_main(input_queue: Queue, output_queue: Queue):
    while True:
        msg = input_queue.get()
        if msg is None:
            break
        msg_type, cmd_id, command = msg
        try:
            # Simulate execution
            time.sleep(0.1)
            if "sleep" in command:
                import re
                match = re.search(r"sleep (\d+)", command)
                if match:
                    time.sleep(float(match.group(1)))
            
            # In a real implementation, this would run the command
            output = f"Executed: {command}"
            output_queue.put(('exec_result', cmd_id, output, "", f"'{command}'"))
        except Exception as e:
            output_queue.put(('error', cmd_id, str(e)))

class BaseShell:
    def __init__(self, on_result_callback: Optional[Callable] = None):
        self.running = True
        self.on_result_callback = on_result_callback
        self.result_queue = queue.Queue()
        self._input_queue = None
        self._output_queue = None
        self._process = None
        self._command_id = 0
        self._cid_lock = threading.Lock()
        self._pending = {}
        self._executor = ThreadPoolExecutor(max_workers=5)
        self._start_worker()

    def _start_worker(self):
        self._stop_worker()
        self._input_queue = Queue()
        self._output_queue = Queue()
        self._process = Process(target=_worker_main, args=(self._input_queue, self._output_queue))
        self._process.daemon = True
        self._process.start()

    def _stop_worker(self):
        if self._input_queue:
            try:
                self._input_queue.put(None, timeout=1)
            except:
                pass
        if self._process and self._process.is_alive():
            self._process.terminate()
            self._process.join()
        self._process = None

    def _ensure_worker(self):
        if self._process is None or not self._process.is_alive():
            self._start_worker()

    def submit_command(self, command: str, exec_id: str = None, timeout: float = 30) -> str:
        if exec_id is None:
            exec_id = f"cmd_{int(time.time() * 1000)}"
        self._ensure_worker()
        with self._cid_lock:
            self._command_id += 1
            cmd_id = self._command_id
        self._pending[cmd_id] = exec_id

        def _run():
            try:
                self._input_queue.put(('exec', cmd_id, command), timeout=5)
            except Exception as e:
                self.result_queue.put({"exec_id": exec_id, "error": str(e), "status": "error"})
                return

            deadline = time.time() + timeout
            while time.time() < deadline:
                try:
                    # This is the problematic part in the original code
                    msg = self._output_queue.get(timeout=1)
                    if msg[1] == cmd_id:
                        # Found it
                        if msg[0] == 'exec_result':
                            self.result_queue.put({"exec_id": exec_id, "result": msg[2], "status": "completed"})
                        else:
                            self.result_queue.put({"exec_id": exec_id, "error": msg[2], "status": "error"})
                        self._pending.pop(cmd_id, None)
                        return
                    else:
                        # WRONG: Discarding message that belongs to someone else!
                        pass
                except queue.Empty:
                    continue
            
            self.result_queue.put({"exec_id": exec_id, "status": "timeout"})
            self._pending.pop(cmd_id, None)

        self._executor.submit(_run)
        return exec_id

    def shutdown(self):
        self.running = False
        self._stop_worker()
        self._executor.shutdown(wait=False)

class PythonShell(BaseShell):
    pass

if __name__ == "__main__":
    shell = PythonShell()
    # Test concurrency
    shell.submit_command("sleep 1", "cmd0")
    shell.submit_command("echo hi", "cmd1")
    
    results = []
    for _ in range(2):
        results.append(shell.result_queue.get(timeout=5))
    
    print(results)
    shell.shutdown()
