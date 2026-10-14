// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;
// Read-only sell simulator. Only ever executed inside eth_call with a state override (never deployed, never holds funds).
interface IERC20 { function balanceOf(address) external view returns (uint); function transfer(address,uint) external returns (bool); }
interface IWETH { function deposit() external payable; }
interface IV2Pair { function token0() external view returns (address); function getReserves() external view returns (uint112,uint112,uint32);
                    function swap(uint,uint,address,bytes calldata) external; }
interface IV3Pool { function token0() external view returns (address);
                    function swap(address,bool,int256,uint160,bytes calldata) external returns (int256,int256); }
interface IAero { function getAmountOut(uint, address) external view returns (uint); }
struct PoolKey { address currency0; address currency1; uint24 fee; int24 tickSpacing; address hooks; }
struct SwapParams { bool zeroForOne; int256 amountSpecified; uint160 sqrtPriceLimitX96; }
interface IPM { function unlock(bytes calldata) external returns (bytes memory);
                function swap(PoolKey memory, SwapParams memory, bytes calldata) external returns (int256);
                function sync(address) external; function settle() external payable returns (uint); function take(address, address, uint) external; }
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
    // Aerodrome / Velodrome V2 pools (volatile + stable): the pool prices itself via getAmountOut
    function aero(address pair, address token, address weth, uint amountIn) external payable
        returns (uint expBuy, uint gotBuy, uint toPair, uint expSell, uint gotSell) {
        IWETH(weth).deposit{value: amountIn}();
        bool t0 = IV2Pair(pair).token0() == token;
        uint ask = IAero(pair).getAmountOut(amountIn, weth) * 99 / 100; expBuy = ask;
        IERC20(weth).transfer(pair, amountIn);
        uint b0 = IERC20(token).balanceOf(address(this));
        IV2Pair(pair).swap(t0 ? ask : 0, t0 ? 0 : ask, address(this), "");
        gotBuy = IERC20(token).balanceOf(address(this)) - b0;
        expSell = IAero(pair).getAmountOut(gotBuy, token);
        uint p0 = IERC20(token).balanceOf(pair);
        IERC20(token).transfer(pair, gotBuy);
        toPair = IERC20(token).balanceOf(pair) - p0;
        uint out = IAero(pair).getAmountOut(toPair, token) * 99 / 100;
        uint w0 = IERC20(weth).balanceOf(address(this));
        IV2Pair(pair).swap(t0 ? 0 : out, t0 ? out : 0, address(this), "");
        gotSell = IERC20(weth).balanceOf(address(this)) - w0;
    }
    // Uniswap V4 (PoolManager singleton; native ETH or WETH side). Buy then sell inside one unlock.
    address pm_;
    function v4(address pm, PoolKey calldata key, address token, uint amountIn) external payable returns (uint gotBuy, uint gotSell) {
        pm_ = pm;
        bytes memory r = IPM(pm).unlock(abi.encode(key, token, amountIn));
        (gotBuy, gotSell) = abi.decode(r, (uint, uint));
    }
    function _pay(address cur, uint amt) internal {
        if (cur == address(0)) { IPM(pm_).settle{value: amt}(); }
        else { IPM(pm_).sync(cur); IERC20(cur).transfer(pm_, amt); IPM(pm_).settle(); }
    }
    function _bal(address cur) internal view returns (uint) { return cur == address(0) ? address(this).balance : IERC20(cur).balanceOf(address(this)); }
    function _swap(PoolKey memory key, bool z, uint amt) internal returns (uint got) {
        int256 d = IPM(pm_).swap(key, SwapParams(z, -int256(amt), z ? MIN_SQRT : MAX_SQRT), "");
        int128 a0 = int128(d >> 128); int128 a1 = int128(d);
        (address pin, address pout) = z ? (key.currency0, key.currency1) : (key.currency1, key.currency0);
        int128 owe = z ? a0 : a1; int128 rec = z ? a1 : a0;
        _pay(pin, uint(uint128(-owe)));
        uint b0 = _bal(pout);
        IPM(pm_).take(pout, address(this), uint(uint128(rec)));
        got = _bal(pout) - b0;
    }
    function unlockCallback(bytes calldata data) external returns (bytes memory) {
        require(msg.sender == pm_, "pm");
        (PoolKey memory key, address token, uint amountIn) = abi.decode(data, (PoolKey, address, uint));
        bool tokIs0 = key.currency0 == token;
        address pay = tokIs0 ? key.currency1 : key.currency0;
        if (pay != address(0)) IWETH(pay).deposit{value: amountIn}();
        uint gb = _swap(key, !tokIs0, amountIn);
        uint gs = _swap(key, tokIs0, gb);
        return abi.encode(gb, gs);
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
