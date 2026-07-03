"""数据源连通性测试 — 验证各接口是否正常

用法: python -m scripts.smoke_test
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_mootdx():
    """测试 mootdx 通达信连接"""
    print("\n[1/3] 测试 mootdx (TCP 7709)...")
    try:
        from src.datasource.mootdx_source import fetch_daily_bars, get_client

        client = get_client()
        print("  ✓ mootdx 连接成功")

        # 拉茅台最近5根K线
        df = fetch_daily_bars("600519", count=5)
        if df is not None and not df.empty:
            print(f"  ✓ K线数据正常: {len(df)} 条")
            print(f"    最新: {df.iloc[-1].to_dict()}")
        else:
            print("  ✗ K线数据为空")
            return False
        return True
    except Exception as e:
        print(f"  ✗ mootdx 失败: {e}")
        return False


def test_tencent():
    """测试腾讯财经接口"""
    print("\n[2/3] 测试腾讯财经 (HTTP)...")
    try:
        from src.datasource.tencent_source import fetch_realtime_quotes

        quotes = fetch_realtime_quotes(["600519", "000858"])
        if quotes:
            print(f"  ✓ 腾讯行情正常: {len(quotes)} 只")
            for code, q in quotes.items():
                print(
                    f"    {q['name']}({code}): "
                    f"¥{q['price']} PE={q['pe_ttm']} PB={q['pb']}"
                )
        else:
            print("  ✗ 腾讯行情为空")
            return False
        return True
    except Exception as e:
        print(f"  ✗ 腾讯接口失败: {e}")
        return False


def test_eastmoney():
    """测试东财接口"""
    print("\n[3/3] 测试东方财富 (HTTP, 限流)...")
    try:
        from src.datasource.eastmoney_source import fetch_stock_info

        info = fetch_stock_info("600519")
        if info and info.get("name"):
            print(f"  ✓ 东财接口正常")
            print(
                f"    {info['name']}({info['code']}): "
                f"行业={info['industry']} 市值={info.get('mcap', 0)}"
            )
        else:
            print("  ✗ 东财接口返回空")
            return False
        return True
    except Exception as e:
        print(f"  ✗ 东财接口失败: {e}")
        return False


def main():
    print("=" * 50)
    print("  Sparrow 数据源连通性测试")
    print("=" * 50)

    results = {
        "mootdx": test_mootdx(),
        "tencent": test_tencent(),
        "eastmoney": test_eastmoney(),
    }

    print("\n" + "=" * 50)
    print("  测试结果汇总")
    print("=" * 50)
    all_pass = True
    for name, ok in results.items():
        status = "✓ PASS" if ok else "✗ FAIL"
        print(f"  {name:12s}: {status}")
        if not ok:
            all_pass = False

    if all_pass:
        print("\n  🎉 全部通过! 可以开始采集数据。")
    else:
        print("\n  ⚠️  部分数据源不可用，请检查网络环境。")
        print("     mootdx 需要国内网络(TCP 7709)")

    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
