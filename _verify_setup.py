# 一次性环境验证脚本：验证导入链、四个数据集加载、评测全链路（跑完即删）
from reporting import format_check_correctness_result


GOOD = (
    "from typing import List\n"
    "def has_close_elements(numbers: List[float], threshold: float) -> bool:\n"
    "    for idx, elem in enumerate(numbers):\n"
    "        for idx2, elem2 in enumerate(numbers):\n"
    "            if idx != idx2:\n"
    "                if abs(elem - elem2) < threshold:\n"
    "                    return True\n"
    "    return False\n"
)
BAD = GOOD.replace("< threshold", "<= threshold * 0.01")


def main():
    from config import DATASET_PATHS
    from src.dataset_loader import load_dataset
    from problem_processor import _load_check_correctness_func

    # 1) 四个数据集全部加载
    counts = {}
    for name in ['humaneval', 'humanevalplus', 'bigcodebench', 'classeval']:
        ds = load_dataset(name, DATASET_PATHS[name]['data_path'])
        counts[name] = len(ds)
        print(f"[1] {name} 加载 OK：{len(ds)} 道题")

    # 2) HumanEval 原版数据正误判定
    check = _load_check_correctness_func('humaneval')
    p = load_dataset('humaneval', DATASET_PATHS['humaneval']['data_path'])['HumanEval/0']
    r1 = check(p, GOOD, 10)
    r2 = check(p, BAD, 10)
    print(f"[2] HumanEval 原版：正确解 -> {format_check_correctness_result(r1)} | 错误解 -> {format_check_correctness_result(r2)}")
    assert r1.get('passed') is True and r2.get('passed') is False

    # 3) HumanEval+ parquet 数据全链路（走同一评测模块）
    plus = load_dataset('humanevalplus', DATASET_PATHS['humanevalplus']['data_path'])
    task0 = next(v for k, v in plus.items() if '0' in k.split('/')[-1])
    r3 = check(task0, GOOD, 30)
    r4 = check(task0, BAD, 30)
    print(f"[3] HumanEval+：正确解 -> {format_check_correctness_result(r3)} | 错误解 -> {format_check_correctness_result(r4)}")
    assert r3.get('passed') is True and r4.get('passed') is False

    # 4) ClassEval / BigCodeBench 评测模块可在 Windows 导入
    f_cls = _load_check_correctness_func('classeval')
    f_bcb = _load_check_correctness_func('bigcodebench')
    print(f"[4] ClassEval 评测函数 OK（{f_cls.__module__}）| BigCodeBench 评测函数 OK（{f_bcb.__module__}）")

    print("== 全部验证通过：只差在 .env 里填入 API key 即可开始复验 ==")


if __name__ == '__main__':
    main()
