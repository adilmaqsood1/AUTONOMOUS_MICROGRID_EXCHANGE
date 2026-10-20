const { expect } = require("chai");
const { ethers } = require("hardhat");

describe("Microgrid Transactive Smart Contracts Suite", function () {
  let deployer, seller, buyer, oracleAcc, maliciousAcc;
  let gridUsd, registry, settlement, oracle, rec, reserve;

  const ASSET_SOLAR = ethers.keccak256(ethers.toUtf8Bytes("ASSET_SOLAR_01"));
  const ASSET_CONSUMER = ethers.keccak256(ethers.toUtf8Bytes("ASSET_CONSUMER_01"));
  const METER_SOLAR = ethers.keccak256(ethers.toUtf8Bytes("METER_SOLAR_01"));
  const METER_CONSUMER = ethers.keccak256(ethers.toUtf8Bytes("METER_CONSUMER_01"));

  beforeEach(async function () {
    [deployer, seller, buyer, oracleAcc, maliciousAcc] = await ethers.getSigners();

    // 1. GridUSD
    const GridUSDFactory = await ethers.getContractFactory("GridUSD");
    gridUsd = await GridUSDFactory.deploy(deployer.address);
    await gridUsd.waitForDeployment();

    // 2. MarketRegistry
    const RegistryFactory = await ethers.getContractFactory("MarketRegistry");
    registry = await RegistryFactory.deploy(deployer.address);
    await registry.waitForDeployment();

    // 3. EnergySettlement
    const SettlementFactory = await ethers.getContractFactory("EnergySettlement");
    settlement = await SettlementFactory.deploy(deployer.address, await gridUsd.getAddress(), await registry.getAddress());
    await settlement.waitForDeployment();

    // 4. DeliveryOracle
    const OracleFactory = await ethers.getContractFactory("DeliveryOracle");
    oracle = await OracleFactory.deploy(deployer.address, await settlement.getAddress());
    await oracle.waitForDeployment();

    // 5. RenewableEnergyCertificate
    const RecFactory = await ethers.getContractFactory("RenewableEnergyCertificate");
    rec = await RecFactory.deploy(deployer.address);
    await rec.waitForDeployment();

    // 6. ReserveSettlement
    const ReserveFactory = await ethers.getContractFactory("ReserveSettlement");
    reserve = await ReserveFactory.deploy(await gridUsd.getAddress(), await registry.getAddress());
    await reserve.waitForDeployment();

    // Permissions setup
    const SETTLEMENT_ORACLE_ROLE = await settlement.ORACLE_ROLE();
    await settlement.grantRole(SETTLEMENT_ORACLE_ROLE, await oracle.getAddress());

    const DELIVERY_ORACLE_ROLE = await oracle.ORACLE_ROLE();
    await oracle.grantRole(DELIVERY_ORACLE_ROLE, oracleAcc.address);

    const REG_ORACLE_ROLE = await registry.ORACLE_ROLE();
    await registry.grantRole(REG_ORACLE_ROLE, oracleAcc.address);
    await registry.grantRole(REG_ORACLE_ROLE, await oracle.getAddress());

    // Register participants
    await registry.registerParticipant(seller.address, "SELLER_PROSUMER", "PROSUMER");
    await registry.registerParticipant(buyer.address, "BUYER_CONSUMER", "CONSUMER");

    // Register assets
    await registry.registerAsset(ASSET_SOLAR, seller.address, "SOLAR_PV", METER_SOLAR, true);
    await registry.registerAsset(ASSET_CONSUMER, buyer.address, "RESIDENTIAL_LOAD", METER_CONSUMER, false);

    // Register meters
    await registry.registerMeter(METER_SOLAR, ASSET_SOLAR, seller.address);
    await registry.registerMeter(METER_CONSUMER, ASSET_CONSUMER, buyer.address);

    // Fund accounts with GUSD
    const fundAmount = ethers.parseEther("10000"); // 10,000 GUSD
    await gridUsd.mint(seller.address, fundAmount);
    await gridUsd.mint(buyer.address, fundAmount);
    await gridUsd.mint(deployer.address, fundAmount);
  });

  describe("1. MarketRegistry & Identities", function () {
    it("should correctly store participant and asset identities", async function () {
      expect(await registry.isParticipantActive(seller.address)).to.be.true;
      expect(await registry.isParticipantActive(buyer.address)).to.be.true;
      expect(await registry.getAssetOwner(ASSET_SOLAR)).to.equal(seller.address);
      expect(await registry.isMeterAuthorized(METER_SOLAR, ASSET_SOLAR)).to.be.true;
    });

    it("should prevent unauthorized callers from registering assets", async function () {
      const dummyAsset = ethers.keccak256(ethers.toUtf8Bytes("DUMMY"));
      await expect(
        registry.connect(maliciousAcc).registerAsset(dummyAsset, maliciousAcc.address, "WIND", dummyAsset, true)
      ).to.be.reverted;
    });
  });

  describe("2. EnergySettlement Full & Partial Lifecycles", function () {
    it("should complete a 100% full delivery commitment with payout", async function () {
      const commitmentId = ethers.keccak256(ethers.toUtf8Bytes("TX_COMMITMENT_100"));
      const quantityWh = 10000n; // 10 kWh
      const pricePerWh = ethers.parseEther("0.00015"); // 0.15 GUSD per kWh = 0.00015 per Wh
      const escrowAmount = quantityWh * pricePerWh; // 1.5 GUSD
      const collateralAmount = ethers.parseEther("0.3"); // 0.3 GUSD

      const start = Math.floor(Date.now() / 1000) - 100;
      const end = start + 3600;

      // Create commitment
      await settlement.createCommitment(
        commitmentId,
        buyer.address,
        seller.address,
        ASSET_SOLAR,
        ASSET_CONSUMER,
        quantityWh,
        pricePerWh,
        start,
        end,
        collateralAmount
      );

      // Approve escrow and collateral
      await gridUsd.connect(buyer).approve(await settlement.getAddress(), escrowAmount);
      await gridUsd.connect(seller).approve(await settlement.getAddress(), collateralAmount);

      // Fund escrow and collateral
      await settlement.connect(buyer).fundEscrow(commitmentId);

      // Activate commitment
      await settlement.activateCommitment(commitmentId);

      const sellerBalBefore = await gridUsd.balanceOf(seller.address);
      const buyerBalBefore = await gridUsd.balanceOf(buyer.address);

      // Oracle reports 100% delivery via DeliveryOracle
      const evidenceHash = ethers.keccak256(ethers.toUtf8Bytes("METER_PROOF_HASH_100"));
      await oracle.connect(oracleAcc).submitDeliveryAttestation(
        commitmentId,
        METER_SOLAR,
        quantityWh,
        quantityWh, // Delivered 100%
        start,
        end,
        evidenceHash
      );

      // Settle
      await settlement.settleCommitment(commitmentId);

      const sellerBalAfter = await gridUsd.balanceOf(seller.address);
      const buyerBalAfter = await gridUsd.balanceOf(buyer.address);

      // Seller receives payment (1.5 GUSD) + collateral returned (0.3 GUSD) = +1.8 GUSD
      expect(sellerBalAfter - sellerBalBefore).to.equal(escrowAmount + collateralAmount);
      // Buyer spent exactly escrowAmount
      expect(buyerBalBefore - buyerBalAfter).to.equal(0n); // already deducted during fundEscrow

      const comm = await settlement.getCommitment(commitmentId);
      expect(comm.status).to.equal(4n); // SETTLED
    });

    it("should correctly penalize under-delivery and refund buyer in partial delivery", async function () {
      const commitmentId = ethers.keccak256(ethers.toUtf8Bytes("TX_COMMITMENT_PARTIAL"));
      const quantityWh = 20000n; // 20 kWh
      const pricePerWh = ethers.parseEther("0.00010"); // 0.10 GUSD per kWh
      const escrowAmount = quantityWh * pricePerWh; // 2.0 GUSD
      const collateralAmount = ethers.parseEther("0.4"); // 0.4 GUSD

      const start = Math.floor(Date.now() / 1000) - 100;
      const end = start + 3600;

      await settlement.createCommitment(
        commitmentId,
        buyer.address,
        seller.address,
        ASSET_SOLAR,
        ASSET_CONSUMER,
        quantityWh,
        pricePerWh,
        start,
        end,
        collateralAmount
      );

      await gridUsd.connect(buyer).approve(await settlement.getAddress(), escrowAmount);
      await gridUsd.connect(seller).approve(await settlement.getAddress(), collateralAmount);
      await settlement.connect(buyer).fundEscrow(commitmentId);
      await settlement.activateCommitment(commitmentId);

      // Delivered only 12 kWh out of 20 kWh (40% shortfall)
      const deliveredWh = 12000n;
      const evidenceHash = ethers.keccak256(ethers.toUtf8Bytes("METER_PROOF_PARTIAL"));

      await oracle.connect(oracleAcc).submitDeliveryAttestation(
        commitmentId,
        METER_SOLAR,
        quantityWh,
        deliveredWh,
        start,
        end,
        evidenceHash
      );

      const buyerBalBefore = await gridUsd.balanceOf(buyer.address);
      await settlement.settleCommitment(commitmentId);
      const buyerBalAfter = await gridUsd.balanceOf(buyer.address);

      // Buyer receives unspent escrow refund (0.8 GUSD) + 20% shortfall penalty from seller collateral (0.16 GUSD) = 0.96 GUSD
      const shortfallWh = quantityWh - deliveredWh;
      const shortfallValue = shortfallWh * pricePerWh;
      const penalty = (shortfallValue * 20n) / 100n;
      const expectedRefund = shortfallValue + penalty;
      expect(buyerBalAfter - buyerBalBefore).to.equal(expectedRefund);

      const comm = await settlement.getCommitment(commitmentId);
      expect(comm.status).to.equal(5n); // PARTIAL
    });

    it("should handle zero delivery (default) and penalize seller collateral", async function () {
      const commitmentId = ethers.keccak256(ethers.toUtf8Bytes("TX_COMMITMENT_DEFAULT"));
      const quantityWh = 10000n;
      const pricePerWh = ethers.parseEther("0.00020");
      const escrowAmount = quantityWh * pricePerWh; // 2.0 GUSD
      const collateralAmount = ethers.parseEther("0.5"); // 0.5 GUSD

      const start = Math.floor(Date.now() / 1000) - 100;
      const end = start + 3600;

      await settlement.createCommitment(
        commitmentId,
        buyer.address,
        seller.address,
        ASSET_SOLAR,
        ASSET_CONSUMER,
        quantityWh,
        pricePerWh,
        start,
        end,
        collateralAmount
      );

      await gridUsd.connect(buyer).approve(await settlement.getAddress(), escrowAmount);
      await gridUsd.connect(seller).approve(await settlement.getAddress(), collateralAmount);
      await settlement.connect(buyer).fundEscrow(commitmentId);
      await settlement.activateCommitment(commitmentId);

      // Delivered 0 Wh
      const evidenceHash = ethers.keccak256(ethers.toUtf8Bytes("METER_PROOF_ZERO"));
      await oracle.connect(oracleAcc).submitDeliveryAttestation(
        commitmentId,
        METER_SOLAR,
        quantityWh,
        0n,
        start,
        end,
        evidenceHash
      );

      const buyerBalBefore = await gridUsd.balanceOf(buyer.address);
      await settlement.settleCommitment(commitmentId);
      const buyerBalAfter = await gridUsd.balanceOf(buyer.address);

      // Buyer receives 100% escrow refund (2.0 GUSD) + 20% shortfall penalty (0.4 GUSD) = 2.4 GUSD
      const defaultPenalty = (escrowAmount * 20n) / 100n;
      expect(buyerBalAfter - buyerBalBefore).to.equal(escrowAmount + defaultPenalty);

      const comm = await settlement.getCommitment(commitmentId);
      expect(comm.status).to.equal(6n); // FAILED
    });
  });

  describe("3. DeliveryOracle Replay & Authorization Protections", function () {
    it("should reject attestations from unauthorized accounts", async function () {
      const commitmentId = ethers.keccak256(ethers.toUtf8Bytes("TX_UNAUTH"));
      const evidenceHash = ethers.keccak256(ethers.toUtf8Bytes("PROOF_UNAUTH"));

      await expect(
        oracle.connect(maliciousAcc).submitDeliveryAttestation(
          commitmentId,
          METER_SOLAR,
          1000n,
          1000n,
          100,
          200,
          evidenceHash
        )
      ).to.be.reverted;
    });

    it("should prevent replay of identical evidence hash", async function () {
      const commitmentId1 = ethers.keccak256(ethers.toUtf8Bytes("TX_REPLAY_1"));
      const commitmentId2 = ethers.keccak256(ethers.toUtf8Bytes("TX_REPLAY_2"));
      const evidenceHash = ethers.keccak256(ethers.toUtf8Bytes("SHARED_PROOF_HASH"));

      // Setup commitment 1
      await settlement.createCommitment(
        commitmentId1,
        buyer.address,
        seller.address,
        ASSET_SOLAR,
        ASSET_CONSUMER,
        5000n,
        ethers.parseEther("0.0001"),
        100,
        200,
        0n
      );
      await gridUsd.connect(buyer).approve(await settlement.getAddress(), ethers.parseEther("0.5"));
      await settlement.connect(buyer).fundEscrow(commitmentId1);
      await settlement.activateCommitment(commitmentId1);

      // First attestation succeeds
      await oracle.connect(oracleAcc).submitDeliveryAttestation(
        commitmentId1,
        METER_SOLAR,
        5000n,
        5000n,
        100,
        200,
        evidenceHash
      );

      // Replay attempt on same or another commitment must revert
      await expect(
        oracle.connect(oracleAcc).submitDeliveryAttestation(
          commitmentId2,
          METER_SOLAR,
          5000n,
          5000n,
          100,
          200,
          evidenceHash
        )
      ).to.be.revertedWith("Evidence hash already processed");
    });
  });

  describe("4. Renewable Energy Certificate (EAC) & Conservation Invariant", function () {
    it("should issue, transfer, and retire an EAC with duplicate issuance prevention", async function () {
      const certId = ethers.keccak256(ethers.toUtf8Bytes("EAC_SOLAR_001"));
      const evidenceHash = ethers.keccak256(ethers.toUtf8Bytes("METER_GEN_PROOF_001"));
      const quantityWh = 50000n; // 50 kWh

      // Grant oracle REC issuer permission
      const ORACLE_ROLE = await registry.ORACLE_ROLE();
      await registry.grantRole(ORACLE_ROLE, deployer.address);

      // Issue EAC to solar generator
      await rec.issueCertificate(
        certId,
        seller.address,
        ASSET_SOLAR,
        "SOLAR_PHOTOVOLTAIC",
        quantityWh,
        1000,
        2000,
        evidenceHash
      );

      expect(await rec.ownerOf(certId)).to.equal(seller.address);

      // Duplicate issuance attempt using same evidence hash must revert
      const dupCertId = ethers.keccak256(ethers.toUtf8Bytes("EAC_SOLAR_DUP"));
      await expect(
        rec.issueCertificate(
          dupCertId,
          seller.address,
          ASSET_SOLAR,
          "SOLAR_PHOTOVOLTAIC",
          quantityWh,
          1000,
          2000,
          evidenceHash
        )
      ).to.be.revertedWith("Duplicate issuance: evidence already certified");

      // Transfer EAC from seller to buyer
      await rec.connect(seller).transferCertificate(certId, buyer.address);
      expect(await rec.ownerOf(certId)).to.equal(buyer.address);

      // Buyer retires certificate for ESG/renewable claim
      await rec.connect(buyer).retireCertificate(certId, "Q1_2026_CARBON_NEUTRAL_CLAIM");
      expect(await rec.isRetired(certId)).to.be.true;

      // Retired certificate cannot be transferred again
      await expect(
        rec.connect(buyer).transferCertificate(certId, seller.address)
      ).to.be.revertedWith("Retired certificate cannot be transferred");

      // Conservation invariant check
      expect(await rec.totalRenewableWhRetired()).to.equal(quantityWh);
      expect(await rec.totalRenewableWhIssued()).to.equal(await rec.totalRenewableWhRetired());
    });
  });

  describe("5. ReserveSettlement Lifecycle", function () {
    it("should create, activate, and settle operating reserve commitments", async function () {
      const commitmentId = ethers.keccak256(ethers.toUtf8Bytes("RESERVE_FCR_01"));
      const capacityKw = 50n; // 50 kW
      const availabilityPayment = ethers.parseEther("5.0"); // 5 GUSD
      const activationPricePerKwh = ethers.parseEther("0.20"); // 0.20 GUSD / kWh
      const collateralAmount = ethers.parseEther("1.0"); // 1 GUSD
      const now = Math.floor(Date.now() / 1000);

      // Approve collateral and availability
      await gridUsd.connect(seller).approve(await reserve.getAddress(), collateralAmount);
      await gridUsd.connect(deployer).approve(await reserve.getAddress(), availabilityPayment);

      // Create commitment
      await reserve.createReserveCommitment(
        commitmentId,
        seller.address,
        ASSET_SOLAR,
        0, // FREQUENCY_CONTAINMENT (<2s)
        capacityKw,
        2, // 2s response deadline
        availabilityPayment,
        activationPricePerKwh,
        now - 10,
        now + 3600,
        collateralAmount
      );

      // Activate reserve: request 40 kW for 1800s (30 minutes)
      await reserve.activateReserve(commitmentId, 40n, 1800n);

      // Report delivery from authorized oracle: 40 kW delivered in 1s (< 2s deadline)
      const evidenceHash = ethers.keccak256(ethers.toUtf8Bytes("RESERVE_PROOF_01"));
      await reserve.connect(oracleAcc).reportReserveDelivery(
        commitmentId,
        40n,
        1, // 1s response
        evidenceHash
      );

      const sellerBalBefore = await gridUsd.balanceOf(seller.address);
      await gridUsd.connect(deployer).approve(await reserve.getAddress(), ethers.parseEther("10"));
      await reserve.settleReserve(commitmentId);
      const sellerBalAfter = await gridUsd.balanceOf(seller.address);

      // Payout = availability (5.0) + activation (40 kW * 1800s / 3600 = 20 kWh * 0.20 = 4.0 GUSD) + collateral returned (1.0) = 10.0 GUSD
      const expectedPayout = availabilityPayment + ethers.parseEther("4.0") + collateralAmount;
      expect(sellerBalAfter - sellerBalBefore).to.equal(expectedPayout);
    });
  });

  describe("6. Access Control Boundaries & Pausable Security", function () {
    it("should prevent unauthorized callers from modifying policy or pausing", async function () {
      // Malicious user cannot alter settlement policy
      await expect(
        settlement.connect(maliciousAcc).setPolicy(40, 7200)
      ).to.be.reverted;

      // Malicious user cannot pause settlement contract
      await expect(
        settlement.connect(maliciousAcc).pause()
      ).to.be.reverted;

      // Malicious user cannot deactivate a registered participant in MarketRegistry
      await expect(
        registry.connect(maliciousAcc).deactivateParticipant(seller.address)
      ).to.be.reverted;

      // Malicious user cannot revoke a certificate
      const dummyCert = ethers.keccak256(ethers.toUtf8Bytes("DUMMY_CERT"));
      await expect(
        rec.connect(maliciousAcc).revokeCertificate(dummyCert, "Malicious revocation attempt")
      ).to.be.reverted;
    });

    it("should reject contract state transitions while paused", async function () {
      await settlement.pause();

      const commitmentId = ethers.keccak256(ethers.toUtf8Bytes("PAUSE_TEST"));
      await expect(
        settlement.createCommitment(
          commitmentId,
          buyer.address,
          seller.address,
          ASSET_SOLAR,
          ASSET_CONSUMER,
          1000n,
          ethers.parseEther("0.0001"),
          100,
          200,
          0n
        )
      ).to.be.reverted;

      await settlement.unpause();
    });
  });

  describe("7. Timeout Edge Cases & Grace Period Enforcement", function () {
    it("should reject timeout resolution before grace period, and refund buyer after expiry", async function () {
      const commitmentId = ethers.keccak256(ethers.toUtf8Bytes("TIMEOUT_COMMITMENT_TEST"));
      const quantityWh = 5000n;
      const pricePerWh = ethers.parseEther("0.0001");
      const escrowAmount = quantityWh * pricePerWh; // 0.5 GUSD

      const blockNum = await ethers.provider.getBlockNumber();
      const block = await ethers.provider.getBlock(blockNum);
      const start = block.timestamp + 10;
      const end = start + 3600; // 1h delivery

      await settlement.createCommitment(
        commitmentId,
        buyer.address,
        seller.address,
        ASSET_SOLAR,
        ASSET_CONSUMER,
        quantityWh,
        pricePerWh,
        start,
        end,
        0n
      );

      await gridUsd.connect(buyer).approve(await settlement.getAddress(), escrowAmount);
      await settlement.connect(buyer).fundEscrow(commitmentId);
      await settlement.activateCommitment(commitmentId);

      // Attempt timeout BEFORE grace period elapsed: must revert
      await expect(
        settlement.timeoutCommitment(commitmentId)
      ).to.be.revertedWith("Grace period not elapsed");

      // Advance blockchain time past deliveryEnd + timeoutGracePeriod (3600s)
      await ethers.provider.send("evm_setNextBlockTimestamp", [end + 3605]);
      await ethers.provider.send("evm_mine");

      const buyerBalBefore = await gridUsd.balanceOf(buyer.address);
      await settlement.timeoutCommitment(commitmentId);
      const buyerBalAfter = await gridUsd.balanceOf(buyer.address);

      // 100% escrow refunded to buyer
      expect(buyerBalAfter - buyerBalBefore).to.equal(escrowAmount);
      const c = await settlement.getCommitment(commitmentId);
      expect(c.status).to.equal(6n); // FAILED
    });
  });

  describe("8. Token Accounting & Invariant Testing (Zero Wei Leakage)", function () {
    it("should guarantee exact token conservation across multiple commitments without leaving stranded balances", async function () {
      const contractBalStart = await gridUsd.balanceOf(await settlement.getAddress());
      expect(contractBalStart).to.equal(0n);

      // Run 3 different trade scenarios sequentially:
      // Scenario A: 100% delivered
      // Scenario B: 50% partial delivery
      // Scenario C: 0% default delivery

      for (let i = 0; i < 3; i++) {
        const cid = ethers.keccak256(ethers.toUtf8Bytes(`CONSERVATION_LOOP_${i}`));
        const qtyWh = 10000n;
        const pricePerWh = ethers.parseEther("0.0001");
        const escrow = qtyWh * pricePerWh; // 1.0 GUSD
        const collateral = ethers.parseEther("0.2"); // 0.2 GUSD

        await settlement.createCommitment(
          cid,
          buyer.address,
          seller.address,
          ASSET_SOLAR,
          ASSET_CONSUMER,
          qtyWh,
          pricePerWh,
          100,
          200,
          collateral
        );

        await gridUsd.connect(buyer).approve(await settlement.getAddress(), escrow);
        await gridUsd.connect(seller).approve(await settlement.getAddress(), collateral);
        await settlement.connect(buyer).fundEscrow(cid);
        await settlement.activateCommitment(cid);

        const delivered = i === 0 ? qtyWh : (i === 1 ? qtyWh / 2n : 0n);
        const evidence = ethers.keccak256(ethers.toUtf8Bytes(`PROOF_LOOP_${i}`));

        await oracle.connect(oracleAcc).submitDeliveryAttestation(
          cid,
          METER_SOLAR,
          qtyWh,
          delivered,
          100,
          200,
          evidence
        );

        await settlement.settleCommitment(cid);
      }

      // After all 3 commitments are settled, the settlement contract balance MUST return to exactly 0 wei
      const contractBalEnd = await gridUsd.balanceOf(await settlement.getAddress());
      expect(contractBalEnd).to.equal(0n);
    });

    it("should enforce strict conservation invariant on certificate splitting", async function () {
      const origCertId = ethers.keccak256(ethers.toUtf8Bytes("CERT_SPLIT_PARENT"));
      const evidenceHash = ethers.keccak256(ethers.toUtf8Bytes("PROOF_SPLIT_PARENT"));
      const totalWh = 100000n; // 100 kWh

      await rec.issueCertificate(
        origCertId,
        seller.address,
        ASSET_SOLAR,
        "SOLAR_PHOTOVOLTAIC",
        totalWh,
        1000,
        2000,
        evidenceHash
      );

      // Attempt invalid split: 40 kWh + 50 kWh != 100 kWh -> must revert
      const child1 = ethers.keccak256(ethers.toUtf8Bytes("CERT_CHILD_1"));
      const child2 = ethers.keccak256(ethers.toUtf8Bytes("CERT_CHILD_2"));
      await expect(
        rec.connect(seller).splitCertificate(
          origCertId,
          child1,
          child2,
          40000n,
          50000n, // sum is 90k, not 100k
          buyer.address,
          seller.address
        )
      ).to.be.revertedWith("Conservation failed: sum must equal original quantity");

      // Valid split: 40 kWh + 60 kWh == 100 kWh
      await rec.connect(seller).splitCertificate(
        origCertId,
        child1,
        child2,
        40000n,
        60000n,
        buyer.address,
        seller.address
      );

      // Verify child balances and parent retired state
      expect(await rec.ownerOf(child1)).to.equal(buyer.address);
      expect(await rec.ownerOf(child2)).to.equal(seller.address);
      expect(await rec.isRetired(origCertId)).to.be.true;

      const c1 = await rec.getCertificate(child1);
      const c2 = await rec.getCertificate(child2);
      expect(c1.quantityWh + c2.quantityWh).to.equal(totalWh);
    });
  });
});
