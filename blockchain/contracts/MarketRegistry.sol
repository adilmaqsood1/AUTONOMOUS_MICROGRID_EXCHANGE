// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "@openzeppelin/contracts/access/AccessControl.sol";
import "@openzeppelin/contracts/utils/Pausable.sol";

/**
 * @title MarketRegistry
 * @notice On-chain identity, role-based authorization, asset and meter registry for transactive microgrid.
 */
contract MarketRegistry is AccessControl, Pausable {
    bytes32 public constant MARKET_OPERATOR_ROLE = keccak256("MARKET_OPERATOR_ROLE");
    bytes32 public constant GRID_OPERATOR_ROLE = keccak256("GRID_OPERATOR_ROLE");
    bytes32 public constant ORACLE_ROLE = keccak256("ORACLE_ROLE");

    struct ParticipantInfo {
        string participantId;
        string roleName; // e.g. "PROSUMER", "GENERATOR", "CONSUMER", "STORAGE_OPERATOR"
        bool active;
        uint256 registeredAt;
    }

    struct AssetInfo {
        bytes32 assetId;
        address owner;
        string assetType;
        bytes32 meterIdHash;
        bool renewable;
        bool active;
        uint256 registeredAt;
    }

    struct MeterInfo {
        bytes32 meterIdHash;
        bytes32 assetId;
        address owner;
        bool active;
        uint256 registeredAt;
    }

    mapping(address => ParticipantInfo) public participants;
    mapping(bytes32 => AssetInfo) public assets;
    mapping(bytes32 => MeterInfo) public meters;

    event ParticipantRegistered(address indexed wallet, string participantId, string roleName);
    event ParticipantDeactivated(address indexed wallet);
    event AssetRegistered(bytes32 indexed assetId, address indexed owner, string assetType, bytes32 meterIdHash, bool renewable);
    event AssetDeactivated(bytes32 indexed assetId);
    event MeterRegistered(bytes32 indexed meterIdHash, bytes32 indexed assetId, address indexed owner);

    constructor(address admin) {
        _grantRole(DEFAULT_ADMIN_ROLE, admin);
        _grantRole(MARKET_OPERATOR_ROLE, admin);
        _grantRole(GRID_OPERATOR_ROLE, admin);
        _grantRole(ORACLE_ROLE, admin);
    }

    function isAuthorizedOracle(address account) external view returns (bool) {
        return hasRole(ORACLE_ROLE, account);
    }

    function pause() external onlyRole(DEFAULT_ADMIN_ROLE) {
        _pause();
    }

    function unpause() external onlyRole(DEFAULT_ADMIN_ROLE) {
        _unpause();
    }

    function registerParticipant(
        address wallet,
        string calldata participantId,
        string calldata roleName
    ) external onlyRole(MARKET_OPERATOR_ROLE) whenNotPaused {
        require(wallet != address(0), "Invalid wallet address");
        participants[wallet] = ParticipantInfo({
            participantId: participantId,
            roleName: roleName,
            active: true,
            registeredAt: block.timestamp
        });
        emit ParticipantRegistered(wallet, participantId, roleName);
    }

    function deactivateParticipant(address wallet) external onlyRole(MARKET_OPERATOR_ROLE) {
        require(participants[wallet].active, "Participant not active");
        participants[wallet].active = false;
        emit ParticipantDeactivated(wallet);
    }

    function registerAsset(
        bytes32 assetId,
        address owner,
        string calldata assetType,
        bytes32 meterIdHash,
        bool renewable
    ) external onlyRole(MARKET_OPERATOR_ROLE) whenNotPaused {
        require(owner != address(0), "Invalid owner");
        require(assetId != bytes32(0), "Invalid asset ID");
        assets[assetId] = AssetInfo({
            assetId: assetId,
            owner: owner,
            assetType: assetType,
            meterIdHash: meterIdHash,
            renewable: renewable,
            active: true,
            registeredAt: block.timestamp
        });
        emit AssetRegistered(assetId, owner, assetType, meterIdHash, renewable);
    }

    function deactivateAsset(bytes32 assetId) external onlyRole(MARKET_OPERATOR_ROLE) {
        require(assets[assetId].active, "Asset not active");
        assets[assetId].active = false;
        emit AssetDeactivated(assetId);
    }

    function registerMeter(
        bytes32 meterIdHash,
        bytes32 assetId,
        address owner
    ) external onlyRole(MARKET_OPERATOR_ROLE) whenNotPaused {
        require(meterIdHash != bytes32(0), "Invalid meter ID hash");
        meters[meterIdHash] = MeterInfo({
            meterIdHash: meterIdHash,
            assetId: assetId,
            owner: owner,
            active: true,
            registeredAt: block.timestamp
        });
        emit MeterRegistered(meterIdHash, assetId, owner);
    }

    function isParticipantActive(address wallet) external view returns (bool) {
        return participants[wallet].active;
    }

    function getAssetOwner(bytes32 assetId) external view returns (address) {
        require(assets[assetId].active, "Asset inactive or unknown");
        return assets[assetId].owner;
    }

    function isMeterAuthorized(bytes32 meterIdHash, bytes32 assetId) external view returns (bool) {
        return meters[meterIdHash].active && meters[meterIdHash].assetId == assetId;
    }
}
