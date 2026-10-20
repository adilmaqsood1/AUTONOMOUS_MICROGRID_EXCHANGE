// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import "@openzeppelin/contracts/token/ERC20/extensions/ERC20Burnable.sol";
import "@openzeppelin/contracts/access/Ownable.sol";

/**
 * @title GridUSD (GUSD)
 * @notice Development ERC-20 settlement token for Autonomous Transactive Microgrid Exchange.
 * Pegged to $1.00 USD accounting asset (18 decimals).
 */
contract GridUSD is ERC20, ERC20Burnable, Ownable {
    constructor(address initialOwner) ERC20("GridUSD", "GUSD") Ownable(initialOwner) {
        // Mint initial development supply (1,000,000 GUSD) to deployer
        _mint(initialOwner, 1_000_000 * 10 ** decimals());
    }

    /**
     * @notice Mint tokens to target address (restricted to owner or development simulation).
     */
    function mint(address to, uint256 amount) external onlyOwner {
        _mint(to, amount);
    }

    /**
     * @notice Development helper to fund test participant wallets with standard allowance.
     */
    function devMint(address to, uint256 amount) external {
        _mint(to, amount);
    }
}
