"""
accounts_modals.py
종목 수정 모달 — 탭 3개: 정보 / 매수 / 매도
모달은 accounts.py UI 내에 정적 DOM으로 상주하며 JS로만 show/hide.
"""
from shiny import ui
from app.db import get_market_label, get_market_map


def modal_edit_position_html(market_options: str) -> ui.Tag:
    """
    종목 수정 모달 HTML.
    market_options: <option> 태그 문자열 (accounts_ui()에서 생성해 전달)
    JS 진입점:
      acOpenEditPositionModal(el)  — 종목 row 클릭 시 data-* 읽어 필드 채움
      acTriggerEditPositionSave()  — 정보 탭 저장
      acTriggerBuy()               — 매수 확인
      acTriggerSell()              — 매도 확인
      acTriggerPositionDelete()    — 종목 삭제
    """
    return ui.div(
        ui.div(
            # ── 헤더 ──────────────────────────────────────────────────────────
            ui.div(
                ui.p("", id="ac-edit-pos-ticker", class_="ticker-readonly", style="margin:0;"),
                ui.span("✕", class_="modal-close-icon",
                        onclick="acHideModal('ac-modal-edit-position');"),
                class_="modal-header-row",
            ),

            # ── 탭 버튼 ───────────────────────────────────────────────────────
            ui.div(
                ui.tags.button("정보",   class_="ac-tab-btn ac-tab-active",
                               onclick="acSwitchTab('info');"),
                ui.tags.button("매수",   class_="ac-tab-btn",
                               onclick="acSwitchTab('buy');"),
                ui.tags.button("매도",   class_="ac-tab-btn",
                               onclick="acSwitchTab('sell');"),
                class_="ac-tab-bar",
            ),

            # ── 정보 탭 ───────────────────────────────────────────────────────
            ui.div(
                ui.div(
                    ui.tags.label("종목명"),
                    ui.div(
                        ui.tags.input(id="ac-edit-pos-name", type="text",
                                      class_="form-control", style="flex:1;"),
                        ui.tags.button("🔄", id="ac-edit-pos-lookup-btn",
                                       style="margin-left:6px; padding:0; font-size:18px; background:none; border:none; outline:none; cursor:pointer; line-height:1; -webkit-appearance:none;",
                                       onclick="acLookupTickerEdit();"),
                        style="display:flex; align-items:center;",
                    ),
                ),
                ui.div(ui.tags.label("시장"),
                       ui.tags.select(ui.HTML(market_options),
                                      id="ac-edit-pos-market", class_="form-control")),
                ui.div(ui.tags.label("레버리지"),
                       ui.tags.select(
                           ui.tags.option("x1", value="1"),
                           ui.tags.option("x2", value="2"),
                           ui.tags.option("x3", value="3"),
                           id="ac-edit-pos-leverage", class_="form-control",
                       )),
                ui.div(ui.tags.label("수량"),
                       ui.tags.input(id="ac-edit-pos-qty", type="number",
                                     min="0", step="any", inputmode="decimal",
                                     class_="form-control")),
                ui.div(ui.tags.label("평균단가 (직접 입력)"),
                       ui.tags.input(id="ac-edit-pos-avg-price", type="number",
                                     min="0", step="any", inputmode="decimal",
                                     placeholder="미입력 시 유지",
                                     class_="form-control")),
                ui.tags.button("저장", class_="btn-add",
                               onclick="acTriggerEditPositionSave();"),
                ui.tags.button("종목 삭제", class_="btn-modal-delete-bottom",
                               onclick="event.stopPropagation(); acTriggerPositionDelete();"),
                id="ac-tab-info", class_="ac-tab-panel",
            ),

            # ── 매수 탭 ───────────────────────────────────────────────────────
            ui.div(
                ui.div(ui.tags.label("수량"),
                       ui.tags.input(id="ac-buy-qty", type="number",
                                     min="0", step="any", inputmode="decimal",
                                     placeholder="0",
                                     class_="form-control",
                                     oninput="acUpdateBuyPreview();")),
                ui.div(ui.tags.label("단가"),
                       ui.tags.input(id="ac-buy-price", type="number",
                                     min="0", step="any", inputmode="decimal",
                                     placeholder="0",
                                     class_="form-control",
                                     oninput="acUpdateBuyPreview();")),
                # 미리보기
                ui.div(
                    ui.div(
                        ui.span("현재 평단", class_="ac-preview-label"),
                        ui.span("", id="ac-buy-preview-cur-avg", class_="ac-preview-value"),
                    ),
                    ui.div(
                        ui.span("매수 후 수량", class_="ac-preview-label"),
                        ui.span("", id="ac-buy-preview-qty", class_="ac-preview-value"),
                    ),
                    ui.div(
                        ui.span("매수 후 평단", class_="ac-preview-label"),
                        ui.span("", id="ac-buy-preview-avg", class_="ac-preview-value"),
                    ),
                    ui.div(
                        ui.span("", id="ac-buy-preview-cash-label", class_="ac-preview-label"),
                        ui.span("", id="ac-buy-preview-cash", class_="ac-preview-value"),
                    ),
                    ui.div(
                        ui.span("", id="ac-buy-preview-cost-label", class_="ac-preview-label"),
                        ui.span("", id="ac-buy-preview-cost", class_="ac-preview-value negative"),
                    ),
                    class_="ac-preview-box",
                ),
                ui.tags.button("매수 확인", class_="btn-add",
                               onclick="acTriggerBuy();"),
                id="ac-tab-buy", class_="ac-tab-panel", style="display:none;",
            ),

            # ── 매도 탭 ───────────────────────────────────────────────────────
            ui.div(
                ui.div(ui.tags.label("수량"),
                       ui.tags.input(id="ac-sell-qty", type="number",
                                     min="0", step="any", inputmode="decimal",
                                     placeholder="0",
                                     class_="form-control",
                                     oninput="acUpdateSellPreview();")),
                ui.div(ui.tags.label("단가"),
                       ui.tags.input(id="ac-sell-price", type="number",
                                     min="0", step="any", inputmode="decimal",
                                     placeholder="0",
                                     class_="form-control",
                                     oninput="acUpdateSellPreview();")),
                # 미리보기
                ui.div(
                    ui.div(
                        ui.span("보유 수량", class_="ac-preview-label"),
                        ui.span("", id="ac-sell-preview-cur-qty", class_="ac-preview-value"),
                    ),
                    ui.div(
                        ui.span("매도 후 수량", class_="ac-preview-label"),
                        ui.span("", id="ac-sell-preview-qty", class_="ac-preview-value"),
                    ),
                    ui.div(
                        ui.span("", id="ac-sell-preview-cash-label", class_="ac-preview-label"),
                        ui.span("", id="ac-sell-preview-cash", class_="ac-preview-value positive"),
                    ),
                    ui.div(
                        ui.span("", id="ac-sell-preview-pnl-label", class_="ac-preview-label"),
                        ui.span("", id="ac-sell-preview-pnl", class_="ac-preview-value"),
                    ),
                    class_="ac-preview-box",
                ),
                ui.tags.button("매도 확인", class_="btn-add",
                               onclick="acTriggerSell();"),
                id="ac-tab-sell", class_="ac-tab-panel", style="display:none;",
            ),

            class_="modal-box",
            onclick="event.stopPropagation();",
        ),
        id="ac-modal-edit-position",
        class_="modal-overlay",
        style="display:none;",
        onclick="acHideModal('ac-modal-edit-position');",
    )


def modal_edit_position_js() -> str:
    """
    종목 수정 모달 전용 JS.
    accounts.py의 <script> 블록에 포함시킬 문자열로 반환.
    """
    return """
  // ── 종목 수정 모달 상태 ─────────────────────────────────────────────────
  var _editPosId    = null;
  var _editCurQty   = 0;
  var _editCurAvg   = 0;
  var _editMarket   = '';
  var _editCurrency = '';   // 'KRW' or 'USD' — data-currency 원천 속성

  // ── 탭 전환 ──────────────────────────────────────────────────────────────
  window.acSwitchTab = function(tab) {
    var panels = ['info', 'buy', 'sell'];
    panels.forEach(function(t) {
      var panel = document.getElementById('ac-tab-' + t);
      if (panel) panel.style.display = (t === tab) ? '' : 'none';
    });
    document.querySelectorAll('.ac-tab-btn').forEach(function(btn, i) {
      btn.classList.toggle('ac-tab-active', ['info','buy','sell'][i] === tab);
    });
    if (tab === 'buy')  { _resetBuyInputs();  acUpdateBuyPreview(); }
    if (tab === 'sell') { _resetSellInputs(); acUpdateSellPreview(); }
  };

  function _resetBuyInputs() {
    var q = document.getElementById('ac-buy-qty');   if (q) q.value = '';
    var p = document.getElementById('ac-buy-price'); if (p) p.value = '';
  }
  function _resetSellInputs() {
    var q = document.getElementById('ac-sell-qty');   if (q) q.value = '';
    var p = document.getElementById('ac-sell-price'); if (p) p.value = '';
  }

  // ── 보유현금 조회 — data-ticker 기준으로 현금 row에서 data-amount 읽기 ──
  function _getCashAmount(currency) {
    // currency: 'KRW' or 'USD'
    // 현금 row는 onclick="acOpenEditCashModal(this)" + data-ticker=currency
    var rows = document.querySelectorAll('[data-ticker="' + currency + '"]');
    for (var i = 0; i < rows.length; i++) {
      var amt = parseFloat(rows[i].getAttribute('data-amount'));
      if (!isNaN(amt)) return amt;
    }
    return 0;
  }

  // ── 숫자 포맷 헬퍼 ───────────────────────────────────────────────────────
  function _fmtNum(n, currency) {
    if (isNaN(n) || n === null) return '-';
    var suffix = currency ? (' ' + currency) : '';
    return n.toLocaleString('ko-KR', {maximumFractionDigits: 4}) + suffix;
  }

  // ── 모달 열기 (data-* 읽어 필드 채움) ───────────────────────────────────
  window.acOpenEditPositionModal = function(el) {
    _editPosId  = parseInt(el.getAttribute('data-pos-id'));
    _editCurQty = parseFloat(el.getAttribute('data-qty'))       || 0;
    _editCurAvg = parseFloat(el.getAttribute('data-avg-price')) || 0;
    _editMarket   = el.getAttribute('data-market')   || '';
    _editCurrency = el.getAttribute('data-currency') || 'KRW';

    var tEl = document.getElementById('ac-edit-pos-ticker');
    if (tEl) tEl.textContent = el.getAttribute('data-ticker');

    var nEl = document.getElementById('ac-edit-pos-name');
    if (nEl) nEl.value = el.getAttribute('data-name');

    var mEl = document.getElementById('ac-edit-pos-market');
    if (mEl) mEl.value = _editMarket;

    var lEl = document.getElementById('ac-edit-pos-leverage');
    if (lEl) lEl.value = el.getAttribute('data-leverage');

    var qEl = document.getElementById('ac-edit-pos-qty');
    if (qEl) qEl.value = _editCurQty;

    var avgEl = document.getElementById('ac-edit-pos-avg-price');
    if (avgEl) avgEl.value = _editCurAvg || '';

    acSwitchTab('info');
    acShowModal('ac-modal-edit-position');
  };

  // ── 매수 미리보기 ────────────────────────────────────────────────────────
  window.acUpdateBuyPreview = function() {
    var buyQty   = parseFloat(document.getElementById('ac-buy-qty')   ? document.getElementById('ac-buy-qty').value   : 0) || 0;
    var buyPrice = parseFloat(document.getElementById('ac-buy-price') ? document.getElementById('ac-buy-price').value : 0) || 0;

    var newQty   = _editCurQty + buyQty;
    var newAvg   = (newQty > 0)
      ? ((_editCurQty * _editCurAvg) + (buyQty * buyPrice)) / newQty
      : 0;
    var cost     = buyQty * buyPrice;
    var cashHeld = _getCashAmount(_editCurrency);

    var cur = _editCurrency;

    var curAvgEl = document.getElementById('ac-buy-preview-cur-avg');
    if (curAvgEl) curAvgEl.textContent = _fmtNum(_editCurAvg, cur);

    var qEl = document.getElementById('ac-buy-preview-qty');
    if (qEl) qEl.textContent = _fmtNum(newQty, '');

    var avgEl = document.getElementById('ac-buy-preview-avg');
    if (avgEl) avgEl.textContent = _fmtNum(newAvg, cur);

    // 보유현금 레이블 + 값
    var cashLabel = document.getElementById('ac-buy-preview-cash-label');
    if (cashLabel) cashLabel.textContent = '보유현금(' + cur + ')';
    var cashEl = document.getElementById('ac-buy-preview-cash');
    if (cashEl) {
      cashEl.textContent = _fmtNum(cashHeld, cur);
      cashEl.className   = 'ac-preview-value' + (cost > cashHeld ? ' negative' : '');
    }

    // 매수금액 레이블 + 값
    var costLabel = document.getElementById('ac-buy-preview-cost-label');
    if (costLabel) costLabel.textContent = '매수금액(' + cur + ')';
    var costEl = document.getElementById('ac-buy-preview-cost');
    if (costEl) {
      costEl.textContent = '-' + _fmtNum(cost, cur);
      costEl.className   = 'ac-preview-value negative' + (cost > cashHeld ? ' ac-preview-over' : '');
    }
  };

  // ── 매도 미리보기 ────────────────────────────────────────────────────────
  window.acUpdateSellPreview = function() {
    var sellQty   = parseFloat(document.getElementById('ac-sell-qty')   ? document.getElementById('ac-sell-qty').value   : 0) || 0;
    var sellPrice = parseFloat(document.getElementById('ac-sell-price') ? document.getElementById('ac-sell-price').value : 0) || 0;

    var remQty  = _editCurQty - sellQty;
    var cashIn  = sellQty * sellPrice;
    var pnl     = sellQty * (sellPrice - _editCurAvg);
    var pnlCls  = pnl >= 0 ? 'positive' : 'negative';
    var cur     = _editCurrency;

    var curQtyEl = document.getElementById('ac-sell-preview-cur-qty');
    if (curQtyEl) curQtyEl.textContent = _fmtNum(_editCurQty, '');

    var remQtyEl = document.getElementById('ac-sell-preview-qty');
    if (remQtyEl) remQtyEl.textContent = _fmtNum(remQty < 0 ? 0 : remQty, '');

    // 매도금액 레이블 + 값
    var cashLabel = document.getElementById('ac-sell-preview-cash-label');
    if (cashLabel) cashLabel.textContent = '매도금액(' + cur + ')';
    var cashEl = document.getElementById('ac-sell-preview-cash');
    if (cashEl) cashEl.textContent = '+' + _fmtNum(cashIn, cur);

    // 실현손익 레이블 + 값
    var pnlLabel = document.getElementById('ac-sell-preview-pnl-label');
    if (pnlLabel) pnlLabel.textContent = '실현손익(' + cur + ')';
    var pnlEl = document.getElementById('ac-sell-preview-pnl');
    if (pnlEl) {
      pnlEl.textContent = (pnl >= 0 ? '+' : '') + _fmtNum(pnl, cur);
      pnlEl.className   = 'ac-preview-value ' + pnlCls;
    }
  };

  // ── 정보 저장 트리거 ─────────────────────────────────────────────────────
  window.acTriggerEditPositionSave = function() {
    var nEl   = document.getElementById('ac-edit-pos-name');
    var mEl   = document.getElementById('ac-edit-pos-market');
    var lEl   = document.getElementById('ac-edit-pos-leverage');
    var qEl   = document.getElementById('ac-edit-pos-qty');
    var avgEl = document.getElementById('ac-edit-pos-avg-price');
    Shiny.setInputValue(window._acNs + '-btn_confirm_edit_position', {
      pos_id:    _editPosId,
      name:      nEl   ? nEl.value   : '',
      market:    mEl   ? mEl.value   : 'KR',
      leverage:  lEl   ? lEl.value   : '1',
      qty:       qEl   ? (parseFloat(qEl.value)   || 0) : 0,
      avg_price: avgEl ? (parseFloat(avgEl.value) || null) : null,
    }, {priority: 'event'});
    acHideModal('ac-modal-edit-position');
  };

  // ── 매수 확인 트리거 ─────────────────────────────────────────────────────
  window.acTriggerBuy = function() {
    var buyQty   = parseFloat(document.getElementById('ac-buy-qty')   ? document.getElementById('ac-buy-qty').value   : 0) || 0;
    var buyPrice = parseFloat(document.getElementById('ac-buy-price') ? document.getElementById('ac-buy-price').value : 0) || 0;
    if (buyQty <= 0 || buyPrice <= 0) { alert('수량과 단가를 입력하세요.'); return; }
    var cost     = buyQty * buyPrice;
    var cashHeld = _getCashAmount(_editCurrency);
    if (cost > cashHeld) {
      alert('매수금액(' + cost.toLocaleString('ko-KR', {maximumFractionDigits:4}) + ' ' + _editCurrency + ')이 보유현금(' + cashHeld.toLocaleString('ko-KR', {maximumFractionDigits:4}) + ' ' + _editCurrency + ')을 초과합니다.');
      return;
    }
    Shiny.setInputValue(window._acNs + '-btn_confirm_buy', {
      pos_id: _editPosId,
      qty:    buyQty,
      price:  buyPrice,
    }, {priority: 'event'});
    acHideModal('ac-modal-edit-position');
  };

  // ── 매도 확인 트리거 ─────────────────────────────────────────────────────
  window.acTriggerSell = function() {
    var sellQty   = parseFloat(document.getElementById('ac-sell-qty')   ? document.getElementById('ac-sell-qty').value   : 0) || 0;
    var sellPrice = parseFloat(document.getElementById('ac-sell-price') ? document.getElementById('ac-sell-price').value : 0) || 0;
    if (sellQty <= 0 || sellPrice <= 0) { alert('수량과 단가를 입력하세요.'); return; }
    if (sellQty > _editCurQty) { alert('보유 수량(' + _editCurQty + ')을 초과합니다.'); return; }
    Shiny.setInputValue(window._acNs + '-btn_confirm_sell', {
      pos_id: _editPosId,
      qty:    sellQty,
      price:  sellPrice,
    }, {priority: 'event'});
    acHideModal('ac-modal-edit-position');
  };

  // ── 종목 삭제 트리거 ─────────────────────────────────────────────────────
  window.acTriggerPositionDelete = function() {
    if (confirm('종목을 삭제하시겠습니까?')) {
      Shiny.setInputValue(window._acNs + '-confirm_delete_position',
        { pos_id: _editPosId }, {priority: 'event'});
      acHideModal('ac-modal-edit-position');
    }
  };
"""

# ── 계좌 추가 모달 ────────────────────────────────────────────────────────────

def modal_add_account_html() -> ui.Tag:
    return ui.div(
        ui.div(
            ui.div(
                ui.h4("계좌 추가", class_="modal-title"),
                ui.span("✕", class_="modal-close-icon",
                        onclick="acHideModal('ac-modal-add-account');"),
                class_="modal-header-row",
            ),
            ui.div(ui.tags.label("계좌명"),
                   ui.tags.input(id="ac-new-account-name", type="text",
                                 placeholder="예) 키움증권", class_="form-control")),
            ui.div(ui.tags.label("별명 (선택)"),
                   ui.tags.input(id="ac-new-account-alias", type="text",
                                 placeholder="예) 키움", class_="form-control")),
            ui.div(
                ui.tags.input(id="ac-new-account-is-watch", type="checkbox", value="false"),
                ui.tags.label("감시 계좌 (내 자산 아님)",
                              **{"for": "ac-new-account-is-watch"}),
                style="display:flex; align-items:center; gap:8px;",
            ),
            ui.tags.button(
                "추가", class_="btn-add",
                onclick=(
                    "Shiny.setInputValue(window._acNs + '-btn_confirm_add', {"
                    "  name: document.getElementById('ac-new-account-name').value,"
                    "  alias: document.getElementById('ac-new-account-alias').value,"
                    "  is_watch: document.getElementById('ac-new-account-is-watch').checked"
                    "}, {priority: 'event'});"
                    "acHideModal('ac-modal-add-account');"
                ),
            ),
            class_="modal-box",
            onclick="event.stopPropagation();",
        ),
        id="ac-modal-add-account",
        class_="modal-overlay",
        style="display:none;",
        onclick="acHideModal('ac-modal-add-account');",
    )


# ── 종목 추가 모달 ────────────────────────────────────────────────────────────

def modal_add_position_html(market_options: str) -> ui.Tag:
    return ui.div(
        ui.div(
            ui.div(
                ui.h4("종목 추가", class_="modal-title"),
                ui.span("✕", class_="modal-close-icon",
                        onclick="acHideModal('ac-modal-add-position');"),
                class_="modal-header-row",
            ),
            ui.div(
                ui.tags.label("티커"),
                ui.div(
                    ui.tags.input(id="ac-new-pos-ticker", type="text",
                                  placeholder="예) AAPL", class_="form-control",
                                  style="flex:1;",
                                  oninput="this.value=this.value.toUpperCase();"),
                    ui.tags.button("🔍", id="ac-new-pos-lookup-btn",
                                   style="margin-left:6px; padding:0; font-size:18px; background:none; border:none; outline:none; cursor:pointer; line-height:1; -webkit-appearance:none;",
                                   onclick="acLookupTicker();"),
                    style="display:flex; align-items:center;",
                ),
            ),
            ui.div(ui.tags.label("종목명"),
                   ui.tags.input(id="ac-new-pos-name", type="text",
                                 placeholder="예) 애플", class_="form-control")),
            ui.div(ui.tags.label("시장"),
                   ui.tags.select(ui.HTML(market_options),
                                  id="ac-new-pos-market", class_="form-control",
                                  onchange="acUpdateAddPreview();")),
            ui.div(ui.tags.label("레버리지"),
                   ui.tags.select(
                       ui.tags.option("x1", value="1"),
                       ui.tags.option("x2", value="2"),
                       ui.tags.option("x3", value="3"),
                       id="ac-new-pos-leverage", class_="form-control",
                   )),
            ui.div(ui.tags.label("수량"),
                   ui.tags.input(id="ac-new-pos-qty", type="number",
                                 value="0", min="0", step="any", inputmode="decimal",
                                 class_="form-control",
                                 oninput="acUpdateAddPreview();")),
            ui.div(ui.tags.label("매수 평단가"),
                   ui.tags.input(id="ac-new-pos-avg-price", type="number",
                                 min="0", step="any", inputmode="decimal",
                                 placeholder="미입력 시 미설정",
                                 class_="form-control",
                                 oninput="acUpdateAddPreview();")),
            # ── 미리보기 ──────────────────────────────────────────────────────
            ui.div(
                ui.div(
                    ui.span("", id="ac-add-preview-cash-label", class_="ac-preview-label"),
                    ui.span("", id="ac-add-preview-cash", class_="ac-preview-value"),
                ),
                ui.div(
                    ui.span("", id="ac-add-preview-cost-label", class_="ac-preview-label"),
                    ui.span("", id="ac-add-preview-cost", class_="ac-preview-value negative"),
                ),
                ui.div(
                    ui.span("매수 후 잔여현금", class_="ac-preview-label"),
                    ui.span("", id="ac-add-preview-remain", class_="ac-preview-value"),
                ),
                id="ac-add-preview-box",
                class_="ac-preview-box",
            ),
            ui.tags.button(
                "추가", class_="btn-add",
                onclick="acTriggerAddPosition();",
            ),
            class_="modal-box",
            onclick="event.stopPropagation();",
        ),
        id="ac-modal-add-position",
        class_="modal-overlay",
        style="display:none;",
        onclick="acHideModal('ac-modal-add-position');",
    )


# ── 현금 추가 모달 ────────────────────────────────────────────────────────────

def modal_add_cash_html() -> ui.Tag:
    return ui.div(
        ui.div(
            ui.div(
                ui.h4("현금 추가", class_="modal-title"),
                ui.span("✕", class_="modal-close-icon",
                        onclick="acHideModal('ac-modal-add-cash');"),
                class_="modal-header-row",
            ),
            ui.div(ui.tags.label("통화"),
                   ui.tags.select(
                       ui.tags.option("KRW (원화)", value="KRW"),
                       ui.tags.option("USD (달러)", value="USD"),
                       id="ac-new-cash-type", class_="form-control",
                   )),
            ui.div(ui.tags.label("금액"),
                   ui.tags.input(id="ac-new-cash-amount", type="number",
                                 value="0", min="0", step="any", inputmode="decimal",
                                 class_="form-control")),
            ui.tags.button(
                "추가", class_="btn-add",
                onclick=(
                    "Shiny.setInputValue(window._acNs + '-btn_confirm_add_cash', {"
                    "  cash_type: document.getElementById('ac-new-cash-type').value,"
                    "  amount: parseFloat(document.getElementById('ac-new-cash-amount').value) || 0"
                    "}, {priority: 'event'});"
                    "acHideModal('ac-modal-add-cash');"
                ),
            ),
            class_="modal-box",
            onclick="event.stopPropagation();",
        ),
        id="ac-modal-add-cash",
        class_="modal-overlay",
        style="display:none;",
        onclick="acHideModal('ac-modal-add-cash');",
    )


# ── 현금 수정 모달 ────────────────────────────────────────────────────────────

def modal_edit_cash_html() -> ui.Tag:
    return ui.div(
        ui.div(
            ui.div(
                ui.h4("현금 수정", class_="modal-title"),
                ui.span("✕", class_="modal-close-icon",
                        onclick="acHideModal('ac-modal-edit-cash');"),
                class_="modal-header-row",
            ),
            ui.div(ui.tags.label("통화"),
                   ui.tags.select(
                       ui.tags.option("KRW (원화)", value="KRW"),
                       ui.tags.option("USD (달러)", value="USD"),
                       id="ac-edit-cash-type", class_="form-control",
                   )),
            ui.div(ui.tags.label("금액"),
                   ui.tags.input(id="ac-edit-cash-amount", type="number",
                                 min="0", step="any", inputmode="decimal",
                                 class_="form-control")),
            ui.tags.button("저장", class_="btn-add",
                           onclick="acTriggerEditCashSave();"),
            ui.tags.button("현금 삭제", class_="btn-modal-delete-bottom",
                           onclick="event.stopPropagation(); acTriggerCashDelete();"),
            class_="modal-box",
            onclick="event.stopPropagation();",
        ),
        id="ac-modal-edit-cash",
        class_="modal-overlay",
        style="display:none;",
        onclick="acHideModal('ac-modal-edit-cash');",
    )