# 虚拟时钟测试工具（Python 3，仅标准库）

## 文件

- `virtual_clock.py` — 时钟抽象 `Clock` 与两个实现：
  - `VirtualClock`：确定性单线程虚拟时钟，`advance_to_next()` 一步推进到下一个到期事件，`advance_to(t)` 跳到指定时刻，`sleep()` 即推进。
  - `RealClock`：真实时钟，后台单线程从 (deadline, 注册序号) 堆中触发回调，排序语义与虚拟时钟完全一致。
- `retry_logic.py` — 被测示例逻辑 `fetch_with_retry`（每次尝试带超时、失败退避重试），只依赖注入的 `Clock`。
- `test_virtual_clock.py` — 时钟语义自测：同时刻 FIFO、推进中新增定时器的排序、取消、重复定时（无漂移）。
- `test_retry_logic.py` — 示例逻辑在虚拟时钟下的时序测试（无真实等待）。
- `test_parity.py` — 对照测试：同一段逻辑在虚拟与真实时钟下的事件顺序必须一致。真实侧运行在手动推进的时间基上（`DrivenRealClock`，复用 `RealClock` 的调度线程与堆排序），不依赖真实等待，结果确定。

## 语义保证（两个实现一致）

- 同一时刻的多个回调按注册顺序（FIFO）触发。
- 推进过程中新注册的定时器按自身到期时间参与后续排序，不丢失、不提前。
- `TimerHandle.cancel()` 后即使时间推进也绝不触发。
- `call_repeated` 按 `deadline + period` 重排，周期稳定无漂移。

## 运行

```bash
cd /home/administrator/gsb/uid202/A
python3 -m unittest discover -v        # 全部测试
python3 -m unittest test_virtual_clock # 仅时钟语义
python3 -m unittest test_retry_logic   # 示例逻辑时序
python3 -m unittest test_parity        # 虚拟/真实对照
```

## 用法示例

```python
from virtual_clock import VirtualClock

clock = VirtualClock()
clock.call_later(1.0, lambda: print("a"))          # t=1 先注册
clock.call_later(1.0, lambda: print("b"))          # t=1 后注册 -> a 先于 b
h = clock.call_repeated(2.0, lambda: print("tick"))
clock.advance_to_next()   # 推进到 t=1，打印 a b
clock.advance_to(5.0)     # 依次触发 t=2, t=4 的 tick
h.cancel()                # 之后不再触发
```
