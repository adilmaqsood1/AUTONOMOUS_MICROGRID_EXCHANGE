// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "@openzeppelin/contracts/access/AccessControl.sol";
import "@openzeppelin/contracts/utils/Pausable.sol";

interface IEnergySettlement {
    function reportDelivery(bytes32 commitmentId, uint256 deliveredWh, bytes32 evidenceHash) external;
}

/**
 * @title DeliveryOracle
 * @notice Validates cryptographic smart-meter attestations and bridges verified physical delivery on-chain.
 */
contract DeliveryOracle is AccessControl, Pausable {
    bytes32 public constant ORACLE_ROLE = keccak256("ORACLE_ROLE");

    struct Attestation {
        bytes32 commitmentId;
        bytes32 meterIdHash;
        uint256 committedWh;
        uint256 deliveredWh;
        uint256 deliveryStart;
        uint256 deliveryEnd;
        bytes32 evidenceHash;
        uint256 timestamp;
        address oracle;
    }

    IEnergySettlement public settlementContract;
    mapping(bytes32 => Attestation) public attestations;
    mapping(bytes32 => bool) public attestationSubmitted;
    mapping(bytes32 => bool) public evidenceProcessed;

    event DeliveryAttested(
        bytes32 indexed commitmentId,
        bytes32 indexed meterIdHash,
        uint256 deliveredWh,
        bytes32 evidenceHash,
        address indexed oracle
    );

    constructor(address admin, address _settlementContract) {
        require(_settlementContract != address(0), "Invalid settlement contract");
        _grantRole(DEFAULT_ADMIN_ROLE, admin);
        _grantRole(ORACLE_ROLE, admin);
        settlementContract = IEnergySettlement(_settlementContract);
    }

    function pause() external onlyRole(DEFAULT_ADMIN_ROLE) {
        _pause();
    }

    function unpause() external onlyRole(DEFAULT_ADMIN_ROLE) {
        _unpause();
    }

    function setSettlementContract(address _settlementContract) external onlyRole(DEFAULT_ADMIN_ROLE) {
        require(_settlementContract != address(0), "Invalid settlement contract");
        settlementContract = IEnergySettlement(_settlementContract);
    }

    function submitDeliveryAttestation(
        bytes32 commitmentId,
        bytes32 meterIdHash,
        uint256 committedWh,
        uint256 deliveredWh,
        uint256 deliveryStart,
        uint256 deliveryEnd,
        bytes32 evidenceHash
    ) external onlyRole(ORACLE_ROLE) whenNotPaused {
        require(commitmentId != bytes32(0), "Invalid commitment ID");
        require(!attestationSubmitted[commitmentId], "Duplicate attestation for commitment");
        require(!evidenceProcessed[evidenceHash], "Evidence hash already processed");
        require(deliveryEnd >= deliveryStart, "Invalid delivery window");
        require(evidenceHash != bytes32(0), "Missing canonical evidence hash");

        evidenceProcessed[evidenceHash] = true;
        attestationSubmitted[commitmentId] = true;
        attestations[commitmentId] = Attestation({
            commitmentId: commitmentId,
            meterIdHash: meterIdHash,
            committedWh: committedWh,
            deliveredWh: deliveredWh,
            deliveryStart: deliveryStart,
            deliveryEnd: deliveryEnd,
            evidenceHash: evidenceHash,
            timestamp: block.timestamp,
            oracle: msg.sender
        });

        // Trigger on-chain delivery state transition
        settlementContract.reportDelivery(commitmentId, deliveredWh, evidenceHash);

        emit DeliveryAttested(commitmentId, meterIdHash, deliveredWh, evidenceHash, msg.sender);
    }

    function getAttestation(bytes32 commitmentId) external view returns (Attestation memory) {
        require(attestationSubmitted[commitmentId], "Attestation not found");
        return attestations[commitmentId];
    }
}
