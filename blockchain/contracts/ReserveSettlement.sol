// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import "@openzeppelin/contracts/utils/Pausable.sol";
import "./MarketRegistry.sol";

/**
 * @title ReserveSettlement
 * @notice On-chain economic settlement for microgrid operating reserves
 * (FCR, Spinning, Non-Spinning, Replacement) integrating Phase 2 reserve market.
 */
contract ReserveSettlement is ReentrancyGuard, Pausable {
    using SafeERC20 for IERC20;

    enum ReserveServiceType {
        FREQUENCY_CONTAINMENT, // <2s
        SPINNING_RESERVE,      // <30s
        NON_SPINNING,          // <300s
        REPLACEMENT            // <1800s
    }

    enum ReserveStatus {
        COMMITTED,
        ACTIVATED,
        DELIVERY_REPORTED,
        SETTLED,
        FAILED,
        CANCELLED
    }

    struct ReserveCommitment {
        bytes32 commitmentId;
        address provider;
        bytes32 assetId;
        ReserveServiceType serviceType;
        uint256 capacityKw;
        uint256 responseDeadlineSeconds;
        uint256 availabilityPayment;    // GUSD wei for standing by
        uint256 activationPricePerKwh;  // GUSD wei per kWh when activated
        uint256 windowStart;
        uint256 windowEnd;
        uint256 collateralAmount;       // Provider collateral locked
        ReserveStatus status;
        // Activation details
        uint256 requestedKw;
        uint256 activationDurationSeconds;
        uint256 activatedAt;
        // Delivery details
        uint256 deliveredKw;
        uint256 actualResponseSeconds;
        bytes32 evidenceHash;
    }

    IERC20 public immutable settlementToken;
    MarketRegistry public immutable registry;

    // Commitment storage
    mapping(bytes32 => ReserveCommitment) public commitments;
    bytes32[] public commitmentList;

    // Configurable penalty rate basis points (e.g., 2000 = 20% penalty of collateral/shortfall)
    uint256 public nonPerformancePenaltyBps = 2500; // 25% default

    // Events
    event ReserveCommitted(
        bytes32 indexed commitmentId,
        address indexed provider,
        bytes32 indexed assetId,
        ReserveServiceType serviceType,
        uint256 capacityKw,
        uint256 availabilityPayment,
        uint256 collateralAmount
    );

    event ReserveActivated(
        bytes32 indexed commitmentId,
        uint256 requestedKw,
        uint256 durationSeconds,
        uint256 timestamp
    );

    event ReserveDeliveryReported(
        bytes32 indexed commitmentId,
        uint256 deliveredKw,
        uint256 actualResponseSeconds,
        bytes32 evidenceHash
    );

    event ReserveSettled(
        bytes32 indexed commitmentId,
        address indexed provider,
        uint256 availabilityPaid,
        uint256 activationPaid,
        uint256 penaltyDeducted,
        ReserveStatus finalStatus
    );

    event ReserveFailed(bytes32 indexed commitmentId, string reason);
    event PenaltyRateUpdated(uint256 oldBps, uint256 newBps);

    modifier onlyOperator() {
        require(
            registry.hasRole(registry.MARKET_OPERATOR_ROLE(), msg.sender) ||
            registry.hasRole(registry.GRID_OPERATOR_ROLE(), msg.sender),
            "Caller is not authorized operator"
        );
        _;
    }

    modifier onlyOracle() {
        require(registry.isAuthorizedOracle(msg.sender), "Caller is not authorized oracle");
        _;
    }

    constructor(address _settlementToken, address _registry) {
        require(_settlementToken != address(0), "Invalid token address");
        require(_registry != address(0), "Invalid registry address");
        settlementToken = IERC20(_settlementToken);
        registry = MarketRegistry(_registry);
    }

    function pause() external onlyOperator {
        _pause();
    }

    function unpause() external onlyOperator {
        _unpause();
    }

    function setPenaltyRateBps(uint256 _newBps) external onlyOperator {
        require(_newBps <= 10000, "Penalty cannot exceed 100%");
        emit PenaltyRateUpdated(nonPerformancePenaltyBps, _newBps);
        nonPerformancePenaltyBps = _newBps;
    }

    /**
     * @notice Create and fund a reserve commitment.
     * Escrows availability payment from operator/payer and locks collateral from provider.
     */
    function createReserveCommitment(
        bytes32 commitmentId,
        address provider,
        bytes32 assetId,
        ReserveServiceType serviceType,
        uint256 capacityKw,
        uint256 responseDeadlineSeconds,
        uint256 availabilityPayment,
        uint256 activationPricePerKwh,
        uint256 windowStart,
        uint256 windowEnd,
        uint256 collateralAmount
    ) external onlyOperator nonReentrant whenNotPaused {
        require(commitmentId != bytes32(0), "Invalid commitment ID");
        require(commitments[commitmentId].commitmentId == bytes32(0), "Commitment already exists");
        require(provider != address(0), "Invalid provider address");
        require(capacityKw > 0, "Capacity must be > 0");
        require(windowEnd > windowStart, "Invalid time window");

        ReserveCommitment storage c = commitments[commitmentId];
        c.commitmentId = commitmentId;
        c.provider = provider;
        c.assetId = assetId;
        c.serviceType = serviceType;
        c.capacityKw = capacityKw;
        c.responseDeadlineSeconds = responseDeadlineSeconds;
        c.availabilityPayment = availabilityPayment;
        c.activationPricePerKwh = activationPricePerKwh;
        c.windowStart = windowStart;
        c.windowEnd = windowEnd;
        c.collateralAmount = collateralAmount;
        c.status = ReserveStatus.COMMITTED;

        commitmentList.push(commitmentId);

        // Lock collateral from provider if required
        if (collateralAmount > 0) {
            settlementToken.safeTransferFrom(provider, address(this), collateralAmount);
        }

        // Lock availability payment from operator pool
        if (availabilityPayment > 0) {
            settlementToken.safeTransferFrom(msg.sender, address(this), availabilityPayment);
        }

        emit ReserveCommitted(
            commitmentId,
            provider,
            assetId,
            serviceType,
            capacityKw,
            availabilityPayment,
            collateralAmount
        );
    }

    /**
     * @notice Trigger activation of committed reserve capacity during contingency/balancing.
     */
    function activateReserve(
        bytes32 commitmentId,
        uint256 requestedKw,
        uint256 durationSeconds
    ) external onlyOperator whenNotPaused {
        ReserveCommitment storage c = commitments[commitmentId];
        require(c.status == ReserveStatus.COMMITTED, "Commitment not in COMMITTED state");
        require(requestedKw > 0 && requestedKw <= c.capacityKw, "Invalid requested capacity");
        require(block.timestamp >= c.windowStart && block.timestamp <= c.windowEnd, "Outside window");

        c.status = ReserveStatus.ACTIVATED;
        c.requestedKw = requestedKw;
        c.activationDurationSeconds = durationSeconds;
        c.activatedAt = block.timestamp;

        emit ReserveActivated(commitmentId, requestedKw, durationSeconds, block.timestamp);
    }

    /**
     * @notice Delivery oracle reports measured response.
     */
    function reportReserveDelivery(
        bytes32 commitmentId,
        uint256 deliveredKw,
        uint256 actualResponseSeconds,
        bytes32 evidenceHash
    ) external onlyOracle whenNotPaused {
        ReserveCommitment storage c = commitments[commitmentId];
        require(c.status == ReserveStatus.ACTIVATED, "Reserve not activated");
        require(evidenceHash != bytes32(0), "Invalid evidence hash");

        c.deliveredKw = deliveredKw;
        c.actualResponseSeconds = actualResponseSeconds;
        c.evidenceHash = evidenceHash;
        c.status = ReserveStatus.DELIVERY_REPORTED;

        emit ReserveDeliveryReported(commitmentId, deliveredKw, actualResponseSeconds, evidenceHash);
    }

    /**
     * @notice Settle reserve commitment: availability payment + energy activation payment or penalties.
     */
    function settleReserve(bytes32 commitmentId) external nonReentrant whenNotPaused {
        ReserveCommitment storage c = commitments[commitmentId];
        require(
            c.status == ReserveStatus.COMMITTED || c.status == ReserveStatus.DELIVERY_REPORTED,
            "Cannot settle in current state"
        );

        uint256 availabilityToPay = c.availabilityPayment;
        uint256 activationToPay = 0;
        uint256 penalty = 0;
        ReserveStatus finalStatus = ReserveStatus.SETTLED;

        if (c.status == ReserveStatus.COMMITTED) {
            // Case 1: Never activated, standby period completed successfully
            require(block.timestamp >= c.windowEnd, "Commitment window not yet ended");
            // Return collateral and pay full availability payment to provider
            c.status = ReserveStatus.SETTLED;
        } else {
            // Case 2: Activated and delivery reported
            bool responseOnTime = c.actualResponseSeconds <= c.responseDeadlineSeconds;
            uint256 deliveredKw = c.deliveredKw;
            uint256 requestedKw = c.requestedKw;

            if (!responseOnTime || deliveredKw < (requestedKw * 80 / 100)) {
                // Non-performance failure: under-delivered by >20% or late
                finalStatus = ReserveStatus.FAILED;
                penalty = (c.collateralAmount * nonPerformancePenaltyBps) / 10000;
                availabilityToPay = 0; // Forfeit availability payment
            } else {
                // Performance acceptable: calculate energy activation payment
                // energyKwh = (min(deliveredKw, requestedKw) * durationSeconds) / 3600
                uint256 energyKwh = (deliveredKw * c.activationDurationSeconds) / 3600;
                activationToPay = energyKwh * c.activationPricePerKwh;
            }
            c.status = finalStatus;
        }

        // If reserve was activated, settler (operator) funds the activation energy charge
        if (activationToPay > 0) {
            settlementToken.safeTransferFrom(msg.sender, address(this), activationToPay);
        }

        // Payout to provider: availability + activation + remaining collateral
        uint256 providerPayout = availabilityToPay + activationToPay + (c.collateralAmount - penalty);
        if (providerPayout > 0) {
            settlementToken.safeTransfer(c.provider, providerPayout);
        }

        // Penalty / unused availability returned to operator/system
        uint256 operatorRefund = (c.availabilityPayment - availabilityToPay) + penalty;
        if (operatorRefund > 0) {
            settlementToken.safeTransfer(msg.sender, operatorRefund);
        }

        emit ReserveSettled(
            commitmentId,
            c.provider,
            availabilityToPay,
            activationToPay,
            penalty,
            finalStatus
        );
    }

    function getCommitment(bytes32 commitmentId) external view returns (ReserveCommitment memory) {
        return commitments[commitmentId];
    }
}
