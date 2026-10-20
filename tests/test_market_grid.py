from decimal import Decimal as D

from app.grid.network import LineState, NetworkFlowFeasibility
from app.market.clearing import BookOrder, ValidationResult, clear_market


def mk(id, side, qty, price, seq, pid=None, node="n", renew=False, ronly=False):
    return BookOrder(id, pid or id, "a_" + id, node, side, D(qty), D(price), seq,
                     renewable_only=ronly, is_renewable=renew)


def test_price_time_priority_and_partial_fills():
    buys = [mk("b1", "BUY", 10, "0.20", 2), mk("b2", "BUY", 10, "0.20", 1), mk("b3", "BUY", 10, "0.25", 3)]
    sells = [mk("s1", "SELL", 15, "0.10", 1)]
    r = clear_market(buys, sells)
    # highest price first (b3), then earliest among equals (b2), b1 gets nothing
    assert r.fills["b3"] == D("10") and r.fills["b2"] == D("5") and r.fills["b1"] == D("0")
    assert r.fills["s1"] == D("15")
    assert r.matched_quantity == D("15")
    assert r.trades[0].price == D("0.175")   # midpoint of 0.25 / 0.10
    assert r.trades[1].price == D("0.15")
    assert r.clearing_price == D("0.166667")  # (10*0.175 + 5*0.15)/15 quantized to 6 dp


def test_no_cross_no_trade_and_no_self_trade():
    r = clear_market([mk("b", "BUY", 5, "0.05", 1)], [mk("s", "SELL", 5, "0.10", 1)])
    assert r.trades == []
    r = clear_market([mk("b", "BUY", 5, "0.2", 1, pid="x")], [mk("s", "SELL", 5, "0.1", 1, pid="x")])
    assert r.trades == []


def test_renewable_only_buyer_skips_non_renewable():
    r = clear_market([mk("b", "BUY", 5, "0.3", 1, ronly=True)],
                     [mk("cheap", "SELL", 5, "0.05", 1, renew=False), mk("green", "SELL", 5, "0.1", 2, renew=True)])
    assert [t.sell_order_id for t in r.trades] == ["green"]


def test_clearing_is_deterministic_and_does_not_mutate_inputs():
    buys = [mk("b1", "BUY", 10, "0.2", 1), mk("b2", "BUY", 7, "0.18", 2)]
    sells = [mk("s1", "SELL", 8, "0.1", 1), mk("s2", "SELL", 9, "0.12", 2)]
    r1, r2 = clear_market(buys, sells), clear_market(list(reversed(buys)), list(reversed(sells)))
    assert r1.input_hash == r2.input_hash
    assert [(t.buy_order_id, t.sell_order_id, t.quantity, t.price) for t in r1.trades] == \
           [(t.buy_order_id, t.sell_order_id, t.quantity, t.price) for t in r2.trades]
    assert buys[0].remaining == D("10")


def test_validator_partial_rejection_falls_through_to_alternative_seller():
    net = NetworkFlowFeasibility([LineState("l1", "A", "B", 20.0), LineState("l2", "C", "B", 100.0)])
    buys = [BookOrder("b", "pb", "ab", "B", "BUY", D(40), D("0.3"), 1)]
    sells = [BookOrder("far", "pf", "af", "A", "SELL", D(40), D("0.05"), 1, is_renewable=True),
             BookOrder("near", "pn", "an", "C", "SELL", D(40), D("0.10"), 2, is_renewable=True)]

    def grid_validator(buy, sell, qty):
        tr = net.request_transfer(sell.node_id, buy.node_id, float(qty), commit=True)  # window = 1h
        return ValidationResult(D(str(round(tr.accepted_kw, 6))), "GRID_CONGESTION", "line limit")

    r = clear_market(buys, sells, grid_validator)
    by_seller = {t.sell_order_id: t.quantity for t in r.trades}
    assert by_seller == {"far": D("20"), "near": D("20")}
    assert r.rejected_quantity("GRID_CONGESTION") == D("20")
    assert r.fills["b"] == D("40")


def test_grid_max_flow_parallel_paths_and_residual():
    net = NetworkFlowFeasibility([LineState("a", "S", "M", 30), LineState("b", "M", "T", 20),
                                  LineState("c", "S", "T", 15)])
    assert abs(net.available_transfer_kw("S", "T") - 35) < 1e-9
    tr = net.request_transfer("S", "T", 25)
    assert abs(tr.accepted_kw - 25) < 1e-9
    assert abs(net.available_transfer_kw("S", "T") - 10) < 1e-9
    # reverse direction is *helped* by existing flow (counter-flow netting)
    assert net.available_transfer_kw("T", "S") > 35
    net.lines["c"].active = False
    assert abs(net.available_transfer_kw("S", "T") - 10) < 1e-9  # only the S-M-T path remains (10 kW used)
