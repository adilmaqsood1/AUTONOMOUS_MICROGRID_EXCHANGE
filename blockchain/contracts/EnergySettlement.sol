// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import "@openzeppelin/contracts/access/AccessControl.sol";
import "@openzeppelin/contracts/utils/Pausable.sol";

interface IMarketRegistry {
    function isParticipantActive(address wallet) external view returns (bool);
}

/**
 * @title EnergySettlement
 * @notice Enforces on-chain energy commitments, escrow deposits, deterministic delivery settlement,
 * shortfall penalties, and refunds using GridUSD.
 */
contract EnergySettlement is ReentrancyGuard, AccessControl, Pausable {
    using SafeERC20 for IERC20;

    bytes32 public constant OPERATOR_ROLE = keccak256("OPERATOR_ROLE");
    bytes32 public constant ORACLE_ROLE = keccak256("ORACLE_ROLE");

    enum CommitmentStatus {
        CREATED,
        FUNDED,
        ACTIVE,
        DELIVERY_REPORTED,
        SETTLED,
        PARTIAL,
        FAILED,
        CANCELLED
    }

    struct Commitment {
        bytes32 commitmentId;
        address buyer;
        address seller;
        bytes32 buyerAssetId;
        bytes32 sellerAssetId;
        uint256 quantityWh;
        uint256 pricePerWhGUSD;
        uint256 deliveryStart;
        uint256 deliveryEnd;
        uint256 escrowAmount;
        uint256 collateralAmount;
        uint256 deliveredWh;
        CommitmentStatus status;
        uint256 createdAt;
    }

    IERC20 public immutable settlementToken;
    IMarketRegistry public registry;
    uint256 public penaltyRatePercent = 20; // 20% penalty on shortfall energy cost
    uint256 public timeoutGracePeriod = 3600; // 1 hour after deliveryEnd

    mapping(bytes32 => Commitment) public commitments;

    event CommitmentCreated(
        bytes32 indexed commitmentId,
        address indexed buyer,
        address indexed seller,
        bytes32 buyerAssetId,
        bytes32 sellerAssetId,
        uint256 quantityWh,
        uint256 pricePerWhGUSD
    );
    event EscrowFunded(bytes32 indexed commitmentId, uint256 buyerEscrow, uint256 sellerCollateral);
    event CommitmentActivated(bytes32 indexed commitmentId);
    event DeliveryReported(bytes32 indexed commitmentId, uint256 deliveredWh, bytes32 evidenceHash);
    event CommitmentSettled(
        bytes32 indexed commitmentId,
        uint256 sellerPayment,
        uint256 buyerRefund,
        uint256 penaltyDeducted,
        CommitmentStatus finalStatus
    );
    event CommitmentCancelled(bytes32 indexed commitmentId, string reason);
    event PolicyUpdated(uint256 newPenaltyPercent, uint256 newGracePeriod);

    constructor(address admin, address _settlementToken, address _registry) {
        require(_settlementToken != address(0), "Invalid token");
        require(_registry != address(0), "Invalid registry");
        _grantRole(DEFAULT_ADMIN_ROLE, admin);
        _grantRole(OPERATOR_ROLE, admin);
        _grantRole(ORACLE_ROLE, admin);
        settlementToken = IERC20(_settlementToken);
        registry = IMarketRegistry(_registry);
    }

    function pause() external onlyRole(DEFAULT_ADMIN_ROLE) {
        _pause();
    }

    function unpause() external onlyRole(DEFAULT_ADMIN_ROLE) {
        _unpause();
    }

    function setPolicy(uint256 _penaltyPercent, uint256 _gracePeriod) external onlyRole(DEFAULT_ADMIN_ROLE) {
        require(_penaltyPercent <= 100, "Penalty cannot exceed 100%");
        penaltyRatePercent = _penaltyPercent;
        timeoutGracePeriod = _gracePeriod;
        emit PolicyUpdated(_penaltyPercent, _gracePeriod);
    }

    function setRegistry(address _registry) external onlyRole(DEFAULT_ADMIN_ROLE) {
        require(_registry != address(0), "Invalid registry");
        registry = IMarketRegistry(_registry);
    }

    function createCommitment(
        bytes32 commitmentId,
        address buyer,
        address seller,
        bytes32 buyerAssetId,
        bytes32 sellerAssetId,
        uint256 quantityWh,
        uint256 pricePerWhGUSD,
        uint256 deliveryStart,
        uint256 deliveryEnd,
        uint256 collateralAmount
    ) external onlyRole(OPERATOR_ROLE) whenNotPaused {
        require(commitments[commitmentId].createdAt == 0, "Commitment already exists");
        require(buyer != address(0) && seller != address(0), "Invalid parties");
        require(buyer != seller, "Buyer and seller must differ");
        require(quantityWh > 0, "Quantity must be positive");
        require(pricePerWhGUSD > 0, "Price must be positive");
        require(deliveryEnd > deliveryStart, "Invalid delivery window");

        uint256 escrowAmount = quantityWh * pricePerWhGUSD;

        commitments[commitmentId] = Commitment({
            commitmentId: commitmentId,
            buyer: buyer,
            seller: seller,
            buyerAssetId: buyerAssetId,
            sellerAssetId: sellerAssetId,
            quantityWh: quantityWh,
            pricePerWhGUSD: pricePerWhGUSD,
            deliveryStart: deliveryStart,
            deliveryEnd: deliveryEnd,
            escrowAmount: escrowAmount,
            collateralAmount: collateralAmount,
            deliveredWh: 0,
            status: CommitmentStatus.CREATED,
            createdAt: block.timestamp
        });

        emit CommitmentCreated(commitmentId, buyer, seller, buyerAssetId, sellerAssetId, quantityWh, pricePerWhGUSD);
    }

    function fundEscrow(bytes32 commitmentId) external nonReentrant whenNotPaused {
        Commitment storage c = commitments[commitmentId];
        require(c.createdAt > 0, "Unknown commitment");
        require(c.status == CommitmentStatus.CREATED, "Not in CREATED state");

        c.status = CommitmentStatus.FUNDED;

        // Pull escrow from buyer
        if (c.escrowAmount > 0) {
            settlementToken.safeTransferFrom(c.buyer, address(this), c.escrowAmount);
        }
        // Pull collateral from seller if required
        if (c.collateralAmount > 0) {
            settlementToken.safeTransferFrom(c.seller, address(this), c.collateralAmount);
        }

        emit EscrowFunded(commitmentId, c.escrowAmount, c.collateralAmount);
    }

    function activateCommitment(bytes32 commitmentId) external onlyRole(OPERATOR_ROLE) whenNotPaused {
        Commitment storage c = commitments[commitmentId];
        require(c.status == CommitmentStatus.FUNDED, "Must be FUNDED to activate");
        c.status = CommitmentStatus.ACTIVE;
        emit CommitmentActivated(commitmentId);
    }

    function reportDelivery(
        bytes32 commitmentId,
        uint256 deliveredWh,
        bytes32 evidenceHash
    ) external onlyRole(ORACLE_ROLE) whenNotPaused {
        Commitment storage c = commitments[commitmentId];
        require(c.createdAt > 0, "Unknown commitment");
        require(
            c.status == CommitmentStatus.ACTIVE || c.status == CommitmentStatus.FUNDED,
            "Invalid status for delivery report"
        );

        c.deliveredWh = deliveredWh;
        c.status = CommitmentStatus.DELIVERY_REPORTED;

        emit DeliveryReported(commitmentId, deliveredWh, evidenceHash);
    }

    function settleCommitment(bytes32 commitmentId) external nonReentrant whenNotPaused {
        Commitment storage c = commitments[commitmentId];
        require(c.createdAt > 0, "Unknown commitment");
        require(c.status == CommitmentStatus.DELIVERY_REPORTED, "Delivery not reported");

        uint256 payableWh = c.deliveredWh < c.quantityWh ? c.deliveredWh : c.quantityWh;
        uint256 basePayment = payableWh * c.pricePerWhGUSD;

        uint256 penalty = 0;
        CommitmentStatus finalStatus;

        if (c.deliveredWh >= c.quantityWh) {
            finalStatus = CommitmentStatus.SETTLED;
        } else if (c.deliveredWh == 0) {
            finalStatus = CommitmentStatus.FAILED;
            // Shortfall penalty on total value
            uint256 shortfallValue = c.quantityWh * c.pricePerWhGUSD;
            penalty = (shortfallValue * penaltyRatePercent) / 100;
            if (penalty > c.collateralAmount) {
                penalty = c.collateralAmount;
            }
        } else {
            finalStatus = CommitmentStatus.PARTIAL;
            uint256 shortfallWh = c.quantityWh - c.deliveredWh;
            uint256 shortfallValue = shortfallWh * c.pricePerWhGUSD;
            penalty = (shortfallValue * penaltyRatePercent) / 100;
            if (penalty > c.collateralAmount) {
                penalty = c.collateralAmount;
            }
        }

        c.status = finalStatus;

        // Financial allocation
        uint256 buyerRefund = (c.escrowAmount - basePayment) + penalty;
        uint256 sellerPayment = basePayment + (c.collateralAmount - penalty);

        // Conservation check
        assert(buyerRefund + sellerPayment == c.escrowAmount + c.collateralAmount);

        // Transfers
        if (sellerPayment > 0) {
            settlementToken.safeTransfer(c.seller, sellerPayment);
        }
        if (buyerRefund > 0) {
            settlementToken.safeTransfer(c.buyer, buyerRefund);
        }

        emit CommitmentSettled(commitmentId, sellerPayment, buyerRefund, penalty, finalStatus);
    }

    function timeoutCommitment(bytes32 commitmentId) external nonReentrant whenNotPaused {
        Commitment storage c = commitments[commitmentId];
        require(c.createdAt > 0, "Unknown commitment");
        require(
            c.status == CommitmentStatus.FUNDED || c.status == CommitmentStatus.ACTIVE,
            "Cannot timeout settled or reported commitment"
        );
        require(block.timestamp > c.deliveryEnd + timeoutGracePeriod, "Grace period not elapsed");

        c.status = CommitmentStatus.FAILED;

        // Refund buyer 100% escrow
        uint256 buyerRefund = c.escrowAmount;
        // Return seller collateral (timeout means oracle absence, not proven seller fault)
        uint256 sellerRefund = c.collateralAmount;

        if (buyerRefund > 0) {
            settlementToken.safeTransfer(c.buyer, buyerRefund);
        }
        if (sellerRefund > 0) {
            settlementToken.safeTransfer(c.seller, sellerRefund);
        }

        emit CommitmentSettled(commitmentId, 0, buyerRefund, 0, CommitmentStatus.FAILED);
    }

    function cancelCommitment(bytes32 commitmentId, string calldata reason) external onlyRole(OPERATOR_ROLE) {
        Commitment storage c = commitments[commitmentId];
        require(c.status == CommitmentStatus.CREATED, "Can only cancel un-funded commitment");
        c.status = CommitmentStatus.CANCELLED;
        emit CommitmentCancelled(commitmentId, reason);
    }

    function getCommitment(bytes32 commitmentId) external view returns (Commitment memory) {
        return commitments[commitmentId];
    }
}
