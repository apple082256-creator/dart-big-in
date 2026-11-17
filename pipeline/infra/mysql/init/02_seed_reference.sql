-- 참조 데이터 시드 (자동 생성: src.config.REPORTS, src.quality.account_mapping_quality.CORE_ACCOUNTS)
SET NAMES utf8mb4;

-- index_code는 공식 KRX 코드가 확인되기 전까지 사용하는 내부 안정 코드다.
-- 공식 코드와 구성종목은 검증된 KRX 원본 적재 시 별도 갱신한다.
INSERT INTO krx_indices (index_code, index_name, display_name) VALUES
  ('KRX_SEMI', 'KRX 반도체', '반도체'),
  ('KRX_STEEL', 'KRX 철강', '철강'),
  ('KRX_AUTO', 'KRX 자동차', '자동차'),
  ('KRX_HEALTH', 'KRX 헬스케어', '헬스케어'),
  ('KRX_BANK', 'KRX 은행', '은행·금융지주')
AS new ON DUPLICATE KEY UPDATE
  index_name=new.index_name,
  display_name=new.display_name;

INSERT INTO periods (year, report_type, report_code, report_name) VALUES
  (2023, 'Q1', '11013', '1분기보고서'),
  (2023, 'H1', '11012', '반기보고서'),
  (2023, 'Q3', '11014', '3분기보고서'),
  (2023, 'FY', '11011', '사업보고서'),
  (2024, 'Q1', '11013', '1분기보고서'),
  (2024, 'H1', '11012', '반기보고서'),
  (2024, 'Q3', '11014', '3분기보고서'),
  (2024, 'FY', '11011', '사업보고서'),
  (2025, 'Q1', '11013', '1분기보고서'),
  (2025, 'H1', '11012', '반기보고서'),
  (2025, 'Q3', '11014', '3분기보고서'),
  (2025, 'FY', '11011', '사업보고서');

INSERT INTO standard_accounts (standard_account_nm, category, sj_divs, account_ids, aliases, period_nature, display_order) VALUES
  ('매출액', 'income', '["IS", "CIS"]', '["ifrs-full_Revenue"]', '["매출액", "수익매출액", "영업수익"]', 'flow', 1),
  ('매출원가', 'income', '["IS", "CIS"]', '["ifrs-full_CostOfSales"]', '["매출원가"]', 'flow', 2),
  ('매출총이익', 'income', '["IS", "CIS"]', '["ifrs-full_GrossProfit"]', '["매출총이익", "매출총이익손실"]', 'flow', 3),
  ('영업이익', 'income', '["IS", "CIS"]', '["dart_OperatingIncomeLoss"]', '["영업이익", "영업손익", "영업순손익"]', 'flow', 4),
  ('당기순이익', 'income', '["IS", "CIS"]', '["ifrs-full_ProfitLoss"]', '["당기순이익", "당기순손익", "당기순이익손실", "분기순이익", "반기순이익"]', 'flow', 5),
  ('법인세비용차감전순이익', 'income', '["IS", "CIS"]', '["ifrs-full_ProfitLossBeforeTax"]', '["법인세비용차감전순이익", "법인세비용차감전순손익"]', 'flow', 6),
  ('자산총계', 'balance', '["BS"]', '["ifrs-full_Assets"]', '["자산총계"]', 'point_in_time', 7),
  ('부채총계', 'balance', '["BS"]', '["ifrs-full_Liabilities"]', '["부채총계"]', 'point_in_time', 8),
  ('자본총계', 'balance', '["BS"]', '["ifrs-full_Equity"]', '["자본총계"]', 'point_in_time', 9),
  ('유동자산', 'balance', '["BS"]', '["ifrs-full_CurrentAssets"]', '["유동자산"]', 'point_in_time', 10),
  ('유동부채', 'balance', '["BS"]', '["ifrs-full_CurrentLiabilities"]', '["유동부채"]', 'point_in_time', 11),
  ('이익잉여금', 'balance', '["BS"]', '["ifrs-full_RetainedEarnings"]', '["이익잉여금", "이익잉여금결손금"]', 'point_in_time', 12),
  ('현금및현금성자산', 'balance', '["BS"]', '["ifrs-full_CashAndCashEquivalents"]', '["현금및현금성자산"]', 'point_in_time', 13),
  ('유형자산', 'balance', '["BS"]', '["ifrs-full_PropertyPlantAndEquipment"]', '["유형자산"]', 'point_in_time', 14),
  ('무형자산', 'balance', '["BS"]', '["ifrs-full_IntangibleAssetsOtherThanGoodwill", "ifrs-full_IntangibleAssetsAndGoodwill"]', '["무형자산", "영업권이외의무형자산"]', 'point_in_time', 15),
  ('재고자산', 'balance', '["BS"]', '["ifrs-full_Inventories", "ifrs-full_InventoriesTotal"]', '["재고자산", "유동재고자산"]', 'point_in_time', 16),
  ('매출채권', 'balance', '["BS"]', '["dart_ShortTermTradeReceivable", "ifrs-full_CurrentTradeReceivables"]', '["매출채권", "유동매출채권"]', 'point_in_time', 17),
  ('영업활동현금흐름', 'cashflow', '["CF"]', '["ifrs-full_CashFlowsFromUsedInOperatingActivities"]', '["영업활동현금흐름", "영업활동으로인한현금흐름"]', 'flow', 18),
  ('투자활동현금흐름', 'cashflow', '["CF"]', '["ifrs-full_CashFlowsFromUsedInInvestingActivities"]', '["투자활동현금흐름", "투자활동으로인한현금흐름"]', 'flow', 19),
  ('재무활동현금흐름', 'cashflow', '["CF"]', '["ifrs-full_CashFlowsFromUsedInFinancingActivities"]', '["재무활동현금흐름", "재무활동으로인한현금흐름"]', 'flow', 20),
  ('유형자산취득(CAPEX)', 'cashflow', '["CF"]', '["ifrs-full_PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"]', '["유형자산의취득", "유형자산취득"]', 'flow', 21),
  ('무형자산취득', 'cashflow', '["CF"]', '["ifrs-full_PurchaseOfIntangibleAssetsClassifiedAsInvestingActivities"]', '["무형자산의취득", "무형자산취득"]', 'flow', 22),
  ('감가상각비', 'cashflow', '["CF"]', '["ifrs-full_AdjustmentsForDepreciationExpense", "dart_AdjustmentsForDepreciationExpense"]', '["감가상각비"]', 'flow', 23),
  ('연구개발비', 'income', '["IS", "CIS"]', '[]', '["연구개발비", "경상연구개발비"]', 'flow', 24);
