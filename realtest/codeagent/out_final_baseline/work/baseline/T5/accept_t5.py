"""验收 T5：PythonShell 并发提交的命令必须各自收到自己的执行结果，
不允许结果丢失/错配/串超时。"""
import sys
import time

sys.path.insert(0, ".")


def main():
    from sdk.base_shell import PythonShell

    sh = PythonShell()
    try:
        # 5 条交错 sleep 的命令并发提交：每条必须收到自己带标记的结果
        ids = {}
        for i in range(5):
            cmd = f'import time; time.sleep({0.5 * i}); print("RESULT_{i}")'
            ids[sh.submit_command(cmd, timeout=8)] = i
        results = {}
        deadline = time.time() + 12
        while time.time() < deadline and len(results) < 5:
            for r in sh.get_results():
                results[r["exec_id"]] = r
            time.sleep(0.2)
        ok = True
        for eid, i in ids.items():
            r = results.get(eid)
            if r is None:
                print(f"cmd{i}: 无结果（被并发抢走/丢失）")
                ok = False
            elif r.get("status") != "completed" or f"RESULT_{i}" not in str(r.get("result", "")):
                print(f"cmd{i}: 错配/异常 status={r.get('status')} result={str(r.get('result', ''))[:40]!r}")
                ok = False
            else:
                print(f"cmd{i}: OK")
        print("PASS" if ok else "FAIL: 并发命令结果缺失/错配/串扰")
        return 0 if ok else 1
    finally:
        sh.shutdown()


if __name__ == "__main__":
    sys.exit(main())
