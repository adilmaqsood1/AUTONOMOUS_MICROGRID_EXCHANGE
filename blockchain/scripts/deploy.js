const hre = require("hardhat");
const fs = require("fs");
const path = require("path");

async function main() {
  const [deployer] = await hre.ethers.getSigners();
  console.log("Deploying contracts with account:", deployer.address);

  // 1. Deploy GridUSD
  const GridUSD = await hre.ethers.getContractFactory("GridUSD");
  const gridUsd = await GridUSD.deploy(deployer.address);
  await gridUsd.waitForDeployment();
  const gridUsdAddress = await gridUsd.getAddress();
  console.log("GridUSD deployed to:", gridUsdAddress);

  // 2. Deploy MarketRegistry
  const MarketRegistry = await hre.ethers.getContractFactory("MarketRegistry");
  const registry = await MarketRegistry.deploy(deployer.address);
  await registry.waitForDeployment();
  const registryAddress = await registry.getAddress();
  console.log("MarketRegistry deployed to:", registryAddress);

  // 3. Deploy EnergySettlement
  const EnergySettlement = await hre.ethers.getContractFactory("EnergySettlement");
  const settlement = await EnergySettlement.deploy(deployer.address, gridUsdAddress, registryAddress);
  await settlement.waitForDeployment();
  const settlementAddress = await settlement.getAddress();
  console.log("EnergySettlement deployed to:", settlementAddress);

  // 4. Deploy DeliveryOracle
  const DeliveryOracle = await hre.ethers.getContractFactory("DeliveryOracle");
  const oracle = await DeliveryOracle.deploy(deployer.address, settlementAddress);
  await oracle.waitForDeployment();
  const oracleAddress = await oracle.getAddress();
  console.log("DeliveryOracle deployed to:", oracleAddress);

  // 5. Deploy RenewableEnergyCertificate
  const RenewableEnergyCertificate = await hre.ethers.getContractFactory("RenewableEnergyCertificate");
  const rec = await RenewableEnergyCertificate.deploy(deployer.address);
  await rec.waitForDeployment();
  const recAddress = await rec.getAddress();
  console.log("RenewableEnergyCertificate deployed to:", recAddress);

  // 6. Deploy ReserveSettlement
  const ReserveSettlement = await hre.ethers.getContractFactory("ReserveSettlement");
  const reserve = await ReserveSettlement.deploy(gridUsdAddress, registryAddress);
  await reserve.waitForDeployment();
  const reserveAddress = await reserve.getAddress();
  console.log("ReserveSettlement deployed to:", reserveAddress);

  // Grant DeliveryOracle contract ORACLE_ROLE in EnergySettlement
  const SETTLEMENT_ORACLE_ROLE = await settlement.ORACLE_ROLE();
  await settlement.grantRole(SETTLEMENT_ORACLE_ROLE, oracleAddress);

  // Grant oracle role in DeliveryOracle to deployer
  const DELIVERY_ORACLE_ROLE = await oracle.ORACLE_ROLE();
  await oracle.grantRole(DELIVERY_ORACLE_ROLE, deployer.address);

  // Grant registry roles
  const ORACLE_ROLE = await registry.ORACLE_ROLE();
  await registry.grantRole(ORACLE_ROLE, oracleAddress);
  await registry.grantRole(ORACLE_ROLE, deployer.address);
  console.log("Granted roles successfully");

  const deployedInfo = {
    network: hre.network.name,
    chainId: (await hre.ethers.provider.getNetwork()).chainId.toString(),
    deployer: deployer.address,
    timestamp: new Date().toISOString(),
    contracts: {
      GridUSD: gridUsdAddress,
      MarketRegistry: registryAddress,
      EnergySettlement: settlementAddress,
      DeliveryOracle: oracleAddress,
      RenewableEnergyCertificate: recAddress,
      ReserveSettlement: reserveAddress
    }
  };

  const outputPath = path.join(__dirname, "..", "deployed_addresses.json");
  fs.writeFileSync(outputPath, JSON.stringify(deployedInfo, null, 2), "utf8");
  console.log("Deployment manifest saved to:", outputPath);

  const envContent = [
    `BLOCKCHAIN_MODE=evm`,
    `EVM_RPC_URL=http://127.0.0.1:8545`,
    `EVM_CHAIN_ID=31337`,
    `GRID_USD_ADDRESS=${gridUsdAddress}`,
    `MARKET_REGISTRY_ADDRESS=${registryAddress}`,
    `ENERGY_SETTLEMENT_ADDRESS=${settlementAddress}`,
    `DELIVERY_ORACLE_ADDRESS=${oracleAddress}`,
    `EAC_CONTRACT_ADDRESS=${recAddress}`,
    `RESERVE_SETTLEMENT_ADDRESS=${reserveAddress}`,
    `DEPLOYER_ADDRESS=${deployer.address}`
  ].join("\n");

  const envPath = path.join(__dirname, "..", "deployed_addresses.env");
  fs.writeFileSync(envPath, envContent, "utf8");
  console.log("Environment variables saved to:", envPath);
}

main()
  .then(() => process.exit(0))
  .catch((error) => {
    console.error(error);
    process.exit(1);
  });
