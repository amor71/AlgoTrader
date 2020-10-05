import streamlit as st
import asyncio
from os import path
import pygit2
from datetime import date
from liualgotrader.analytics.analysis import load_batch_list, load_batch_symbols
from liualgotrader.common import config
from liualgotrader.backtester import backtest

try:
    config.build_label = pygit2.Repository("../").describe(
        describe_strategy=pygit2.GIT_DESCRIBE_TAGS
    )
except pygit2.GitError:
    import liualgotrader

    config.build_label = liualgotrader.__version__ if hasattr(liualgotrader, "__version__") else ""  # type: ignore


st.title("Back-testing a trading session")

day_to_analyze = st.date_input("pick day to analyze", value=date.today())
try:
    with st.spinner("Loading list of trading sessions"):
        df = load_batch_list(day_to_analyze)
    if df.empty:
        st.error("Select another day")
        st.stop()
except Exception as e:
    st.error("Select another day")
    st.exception(e)
    st.stop()

bid = st.selectbox("select batch to backtest", df)
strategy_name = st.text_input("Enter strategy name")
strategy_fname = st.text_input("Enter strategy filename")

if len(strategy_fname) > 0:
    if not path.exists(strategy_fname):
        st.error("File not found, re-enter")
    else:
        conf_dict = {}
        conf_dict["strategies"] = {}
        conf_dict["strategies"][strategy_name] = {}
        conf_dict["strategies"][strategy_name]["filename"] = strategy_fname

        symbols_df = load_batch_symbols(bid)

        if st.button(f"Back-testing {strategy_name} on {len(symbols_df)} symbols"):
            with st.spinner(f"backtesting.."):
                asyncio.set_event_loop(asyncio.new_event_loop())
                new_id = backtest(bid, symbols_df["symbol"].to_list(), conf_dict)
                st.success(f"new batch-id is {new_id}")