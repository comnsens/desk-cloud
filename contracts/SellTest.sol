// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;
// Read-only sell simulator. Only ever executed inside eth_call with a state override (never deployed, never holds funds).
interface IERC20 { function balanceOf(address) external view returns (uint); function transfer(address,uint) external returns (bool); }
interface IWETH { function deposit() external payable; }
interface IV2Pair { function token0() external view returns (address); function getReserves() external view returns (uint112,uint112,uint32);
                    function swap(uint,uint,address,bytes calldata) external; }
interface IV3Pool { function token0() external view returns (address);
                    function swap(address,bool,int256,uint160,bytes calldata) external returns (int256,int256); }
contract SellTest {
    uint160 constant MIN_SQRT = 4295128740; uint160 constant MAX_SQRT = 1461446703485210103287233721360146371088282710654;
    address pool_; address payToken_;
    function _out(uint ain, uint rin, uint rout, uint feeBps) internal pure returns (uint) {
        uint a = ain * (10000 - feeBps); return a * rout / (rin * 10000 + a);
    }
    // V2-style pair (Uniswap v2, PancakeSwap v2, Aerodrome volatile-compatible pairs with getReserves)
    function v2(address pair, address token, address weth, uint amountIn, uint feeBps) external payable
        returns (uint expBuy, uint gotBuy, uint toPair, uint expSell, uint gotSell) {
        IWETH(weth).deposit{value: amountIn}();
        bool t0 = IV2Pair(pair).token0() == token;
        (uint112 r0, uint112 r1,) = IV2Pair(pair).getReserves();
        (uint rT, uint rW) = t0 ? (uint(r0), uint(r1)) : (uint(r1), uint(r0));
        IERC20(weth).transfer(pair, amountIn);
        expBuy = _out(amountIn, rW, rT, feeBps);
        uint ask = expBuy * 97 / 100;
        uint b0 = IERC20(token).balanceOf(address(this));
        IV2Pair(pair).swap(t0 ? ask : 0, t0 ? 0 : ask, address(this), "");
        gotBuy = IERC20(token).balanceOf(address(this)) - b0;
        expBuy = ask;
        (r0, r1,) = IV2Pair(pair).getReserves();
        (rT, rW) = t0 ? (uint(r0), uint(r1)) : (uint(r1), uint(r0));
        uint p0 = IERC20(token).balanceOf(pair);
        IERC20(token).transfer(pair, gotBuy);
        toPair = IERC20(token).balanceOf(pair) - p0;
        expSell = _out(gotBuy, rT, rW, feeBps);
        uint actualOut = _out(toPair, rT, rW, feeBps) * 97 / 100;
        uint w0 = IERC20(weth).balanceOf(address(this));
        IV2Pair(pair).swap(t0 ? 0 : actualOut, t0 ? actualOut : 0, address(this), "");
        gotSell = IERC20(weth).balanceOf(address(this)) - w0;
        expSell = expSell * 97 / 100;
    }
    // V3-style pool (Uniswap v3, PancakeSwap v3, Aerodrome Slipstream)
    function v3(address pool, address token, address weth, uint amountIn) external payable
        returns (uint gotBuy, uint toPool, uint gotSell, uint sellAsked) {
        IWETH(weth).deposit{value: amountIn}();
        bool t0 = IV3Pool(pool).token0() == token;
        pool_ = pool;
        uint b0 = IERC20(token).balanceOf(address(this));
        payToken_ = weth;
        IV3Pool(pool).swap(address(this), !t0, int256(amountIn), t0 ? MAX_SQRT : MIN_SQRT, "");
        gotBuy = IERC20(token).balanceOf(address(this)) - b0;
        uint p0 = IERC20(token).balanceOf(pool);
        uint w0 = IERC20(weth).balanceOf(address(this));
        payToken_ = token; sellAsked = gotBuy;
        IV3Pool(pool).swap(address(this), t0, int256(gotBuy), t0 ? MIN_SQRT : MAX_SQRT, "");
        toPool = IERC20(token).balanceOf(pool) - p0;
        gotSell = IERC20(weth).balanceOf(address(this)) - w0;
    }
    function _cb(int256 d0, int256 d1) internal {
        require(msg.sender == pool_, "cb");
        uint owe = uint(d0 > 0 ? d0 : d1);
        IERC20(payToken_).transfer(msg.sender, owe);
    }
    function uniswapV3SwapCallback(int256 a, int256 b, bytes calldata) external { _cb(a, b); }
    function pancakeV3SwapCallback(int256 a, int256 b, bytes calldata) external { _cb(a, b); }
    receive() external payable {}
}
