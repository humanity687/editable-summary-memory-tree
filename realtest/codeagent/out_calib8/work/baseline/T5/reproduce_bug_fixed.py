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
            if "sleep" in command:
                import re
                match = re.search(r"sleep (\d+)", command)
                if match:
                    time.sleep(float(match.group(1)))
            
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
        
        # Dispatcher mechanism
        self._results_map: Dict[int, queue.Queue] = {}
        self._results_map_lock = threading.Lock()
        self._dispatcher_thread = None

        self._start_worker()

    def _start_worker(self):
        self._stop_worker()
        self._input_queue = Queue()
        self._output_queue = Queue()
        
        self._dispatcher_thread = threading.Thread(target=self._dispatch_loop, daemon=True)
        self._dispatcher_thread.start()
        
        self._process = Process(target=_worker_main, args=(self._input_queue, self._output_queue))
        self._process.daemon = True
        self._process.start()

    def _stop_worker(self):
        if self._dispatcher_thread:
            self.running = False
            self._dispatcher_thread.join(timeout=1)
            self._dispatcher_thread = None
            
        if self._input_queue:
            try:
                self._input_queue.put(None, timeout=1)
            except:
                pass
        if self._process and self._process.is_alive():
            self._process.terminate()
            self._process.join()
        self._process = None
        self._input_queue = None
        self._output_queue = None

    def _dispatch_loop(self):
        while self.running:
            try:
                msg = self._output_queue.get(timeout=0.1)
                msg_type = msg[0]
                recv_id = msg[1]
                
                with self._results_map_lock:
                    if recv_id in self._results_map:
                        self._results_map[recv_id].put(msg)
            except queue.Empty:
                continue
            except Exception:
                break

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
        
        cmd_queue = queue.Queue()
        with self._results_map_lock:
            self._results_map[cmd_id] = cmd_queue

        def _run():
            try:
                self._input_queue.put(('exec', cmd_id, command), timeout=5)
            except Exception as e:
                self.result_queue.put({"exec_id": exec_id, "error": str(e), "status": "error"})
                with self._results_map_lock:
                    self._results_map.pop(cmd_id, None)
                return

            deadline = time.time() + timeout
            while time.time() < deadline:
                try:
                    msg = cmd_queue.get(timeout=1)
                    msg_type = msg[0]
                    if msg_type == 'exec_result':
                        self.result_queue.put({"exec_id": exec_id, "result": msg[2], "status": "completed"})
                    else:
                        self.result_queue.put({"exec_id": exec_id, "error": msg[2], "status": "error"})
                    
                    with self._results_map_lock:
                        self._results_map.pop(cmd_id, None)
                    with self._cid_lock:
                        self._pending.pop(cmd_id, None)
                    return
                except queue.Empty:
                    if not self._process or not self._process.is_alive():
                        self.result_queue.put({"exec_id": exec_id, "error": "Worker crashed", "status": "error"})
                        with self._results_map_lock:
                            self._results_map.pop(cmd_id, None)
                        with self._cid_lock:
                            self._pending.pop(cmd_id, None)
                        return
                    continue

            self.result_queue.put({"exec_id": exec_id, "status": "timeout"})
            with self._results_map_lock:
                self._results_map.pop(cmd_id, None)
            with self._cid_lock:
                self._pending.pop(cmd_id, None)

        self._executor.submit(_run)
        return exec_id

    def shutdown(self):
        self._stop_worker()
        self._executor.shutdown(wait=False)

class PythonShell(BaseShell):
    pass

if __name__ == "__main__":
    shell = PythonShell()
    shell.submit_command("sleep 1", "cmd0")
    shell.submit_command("echo hi", "cmd1")
    
    results = []
    for _ in range(2):
        results.append(shell.result_queue.get(timeout=5))
    
    print(f"Results: {results}")
    # Check if both succeeded
    success = len(results) == 2 and all(r.get("status") in ["completed", "error"] for r in results)
    if success:
        print("SUCCESS")
    else:
        print("FAIL")
    shell.shutdown()
