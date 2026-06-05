import datetime
from metrics import calculate_exposure_and_ratios, calculate_xirr, calculate_alpha

def test_run():
    print("=== 1. Exposure 및 자산비중 계산기 테스트 ===")
    mock_db_rows = [
        ("KRW", 5000000, 1, 1, "CASH"),           
        ("USD", 1000, 1, 1, "CASH"),              
        ("AAPL", 10, 150000, 1, "US"),            
        ("QLD", 20, 100000, 2, "US"),             
        ("USD/KRW", 1, 1350000, 1, "FX")          
    ]
    usd_krw = 1350.0 
    
    ratios = calculate_exposure_and_ratios(mock_db_rows, usd_krw)
    print(f"총자산: {ratios['total_asset']:,.0f}원")
    print(f"익스포저 비중: {ratios['exposure']:.2%}")
    print(f"현금 비중: {ratios['cash_ratio']:.2%}")
    print(f"레버리지 X1 비중: {ratios['x1_ratio']:.2%}")  # 👈 프린트문 추가!
    print(f"레버리지 X2 비중: {ratios['x2_ratio']:.2%}\n")

    print("=== 2. IRR(연평균 복리 수익률) 계산기 테스트 ===")
    mock_cash_flows = [
        (datetime.date(2025, 6, 19), 100000000.0),  
        (datetime.date(2025, 12, 25), 20000000.0),  
        (datetime.date(2026, 6, 5), -150000000.0)   
    ]
    
    irr_result = calculate_xirr(mock_cash_flows)
    print(f"내 자산의 연평균 복리 수익률(IRR): {irr_result:.2%}\n")

    print("=== 3. 알파(시장 초과 수익률) 계산기 테스트 ===")
    start_snapshot = (100000000, 15000) 
    end_snapshot = (150000000, 18000)   
    
    alpha_result = calculate_alpha(start_snapshot, end_snapshot)
    print(f"나스닥 지수 대비 내 초과 수익률(알파): {alpha_result:.2%}")

if __name__ == "__main__":
    test_run()