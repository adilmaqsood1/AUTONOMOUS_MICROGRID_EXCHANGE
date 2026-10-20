// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "@openzeppelin/contracts/access/AccessControl.sol";
import "@openzeppelin/contracts/utils/Pausable.sol";

/**
 * @title RenewableEnergyCertificate (EAC)
 * @notice Issues, transfers, splits, and retires verified Energy Attribute Certificates.
 * Enforces cryptographic generation uniqueness and prevents double-counting.
 */
contract RenewableEnergyCertificate is AccessControl, Pausable {
    bytes32 public constant ISSUER_ROLE = keccak256("ISSUER_ROLE");

    enum CertificateStatus {
        ISSUED,
        TRANSFERRED,
        RETIRED,
        REVOKED
    }

    struct Certificate {
        bytes32 certificateId;
        bytes32 generatorAssetId;
        string sourceType;
        uint256 quantityWh;
        uint256 generationStart;
        uint256 generationEnd;
        bytes32 meterEvidenceHash;
        address owner;
        CertificateStatus status;
        uint256 issuedAt;
        uint256 retiredAt;
        string retirementPurpose;
    }

    mapping(bytes32 => Certificate) public certificates;
    mapping(bytes32 => bool) public generationEvidenceUsed; // Prevents duplicate EAC issuance

    uint256 public totalRenewableWhIssued;
    uint256 public totalRenewableWhRetired;

    event CertificateIssued(
        bytes32 indexed certificateId,
        address indexed owner,
        bytes32 indexed generatorAssetId,
        string sourceType,
        uint256 quantityWh,
        bytes32 meterEvidenceHash
    );
    event CertificateTransferred(bytes32 indexed certificateId, address indexed from, address indexed to);
    event CertificateSplit(bytes32 indexed originalId, bytes32 indexed newId1, bytes32 indexed newId2, uint256 qty1, uint256 qty2);
    event CertificateRetired(bytes32 indexed certificateId, address indexed owner, uint256 quantityWh, string purpose);
    event CertificateRevoked(bytes32 indexed certificateId, string reason);

    constructor(address admin) {
        _grantRole(DEFAULT_ADMIN_ROLE, admin);
        _grantRole(ISSUER_ROLE, admin);
    }

    function pause() external onlyRole(DEFAULT_ADMIN_ROLE) {
        _pause();
    }

    function unpause() external onlyRole(DEFAULT_ADMIN_ROLE) {
        _unpause();
    }

    function issueCertificate(
        bytes32 certificateId,
        address recipient,
        bytes32 generatorAssetId,
        string calldata sourceType,
        uint256 quantityWh,
        uint256 generationStart,
        uint256 generationEnd,
        bytes32 meterEvidenceHash
    ) external onlyRole(ISSUER_ROLE) whenNotPaused {
        require(certificateId != bytes32(0), "Invalid certificate ID");
        require(recipient != address(0), "Invalid recipient");
        require(quantityWh > 0, "Quantity must be positive");
        require(generationEnd >= generationStart, "Invalid generation window");
        require(meterEvidenceHash != bytes32(0), "Missing meter evidence hash");
        require(!generationEvidenceUsed[meterEvidenceHash], "Duplicate issuance: evidence already certified");
        require(certificates[certificateId].issuedAt == 0, "Certificate already exists");

        generationEvidenceUsed[meterEvidenceHash] = true;

        certificates[certificateId] = Certificate({
            certificateId: certificateId,
            generatorAssetId: generatorAssetId,
            sourceType: sourceType,
            quantityWh: quantityWh,
            generationStart: generationStart,
            generationEnd: generationEnd,
            meterEvidenceHash: meterEvidenceHash,
            owner: recipient,
            status: CertificateStatus.ISSUED,
            issuedAt: block.timestamp,
            retiredAt: 0,
            retirementPurpose: ""
        });

        totalRenewableWhIssued += quantityWh;

        emit CertificateIssued(certificateId, recipient, generatorAssetId, sourceType, quantityWh, meterEvidenceHash);
    }

    function transferCertificate(bytes32 certificateId, address to) external whenNotPaused {
        Certificate storage c = certificates[certificateId];
        require(c.issuedAt > 0, "Certificate not found");
        require(msg.sender == c.owner, "Only current owner can transfer");
        require(to != address(0) && to != c.owner, "Invalid recipient");
        require(c.status != CertificateStatus.RETIRED, "Retired certificate cannot be transferred");
        require(c.status != CertificateStatus.REVOKED, "Revoked certificate cannot be transferred");

        address from = c.owner;
        c.owner = to;
        c.status = CertificateStatus.TRANSFERRED;

        emit CertificateTransferred(certificateId, from, to);
    }

    function retireCertificate(bytes32 certificateId, string calldata purpose) external whenNotPaused {
        Certificate storage c = certificates[certificateId];
        require(c.issuedAt > 0, "Certificate not found");
        require(msg.sender == c.owner, "Only owner can retire certificate");
        require(c.status != CertificateStatus.RETIRED, "Certificate already retired");
        require(c.status != CertificateStatus.REVOKED, "Cannot retire revoked certificate");

        c.status = CertificateStatus.RETIRED;
        c.retiredAt = block.timestamp;
        c.retirementPurpose = purpose;
        totalRenewableWhRetired += c.quantityWh;

        emit CertificateRetired(certificateId, msg.sender, c.quantityWh, purpose);
    }

    function splitCertificate(
        bytes32 originalId,
        bytes32 newId1,
        bytes32 newId2,
        uint256 splitWh1,
        uint256 splitWh2,
        address recipient1,
        address recipient2
    ) external whenNotPaused {
        Certificate storage orig = certificates[originalId];
        require(orig.issuedAt > 0, "Original certificate not found");
        require(msg.sender == orig.owner, "Only owner can split");
        require(orig.status != CertificateStatus.RETIRED && orig.status != CertificateStatus.REVOKED, "Cannot split retired/revoked");
        require(splitWh1 > 0 && splitWh2 > 0, "Split quantities must be positive");
        require(splitWh1 + splitWh2 == orig.quantityWh, "Conservation failed: sum must equal original quantity");
        require(certificates[newId1].issuedAt == 0 && certificates[newId2].issuedAt == 0, "New certificate IDs must be fresh");

        // Mark original as retired/split
        orig.status = CertificateStatus.RETIRED;
        orig.retiredAt = block.timestamp;
        orig.retirementPurpose = "Split into child certificates";

        // Create child 1
        certificates[newId1] = Certificate({
            certificateId: newId1,
            generatorAssetId: orig.generatorAssetId,
            sourceType: orig.sourceType,
            quantityWh: splitWh1,
            generationStart: orig.generationStart,
            generationEnd: orig.generationEnd,
            meterEvidenceHash: orig.meterEvidenceHash,
            owner: recipient1,
            status: CertificateStatus.ISSUED,
            issuedAt: block.timestamp,
            retiredAt: 0,
            retirementPurpose: ""
        });

        // Create child 2
        certificates[newId2] = Certificate({
            certificateId: newId2,
            generatorAssetId: orig.generatorAssetId,
            sourceType: orig.sourceType,
            quantityWh: splitWh2,
            generationStart: orig.generationStart,
            generationEnd: orig.generationEnd,
            meterEvidenceHash: orig.meterEvidenceHash,
            owner: recipient2,
            status: CertificateStatus.ISSUED,
            issuedAt: block.timestamp,
            retiredAt: 0,
            retirementPurpose: ""
        });

        emit CertificateSplit(originalId, newId1, newId2, splitWh1, splitWh2);
    }

    function revokeCertificate(bytes32 certificateId, string calldata reason) external onlyRole(DEFAULT_ADMIN_ROLE) {
        Certificate storage c = certificates[certificateId];
        require(c.issuedAt > 0, "Certificate not found");
        c.status = CertificateStatus.REVOKED;
        emit CertificateRevoked(certificateId, reason);
    }

    function ownerOf(bytes32 certificateId) external view returns (address) {
        require(certificates[certificateId].issuedAt > 0, "Certificate not found");
        return certificates[certificateId].owner;
    }

    function isRetired(bytes32 certificateId) external view returns (bool) {
        require(certificates[certificateId].issuedAt > 0, "Certificate not found");
        return certificates[certificateId].status == CertificateStatus.RETIRED;
    }

    function getCertificate(bytes32 certificateId) external view returns (Certificate memory) {
        require(certificates[certificateId].issuedAt > 0, "Certificate not found");
        return certificates[certificateId];
    }
}
