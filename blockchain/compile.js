const fs = require('fs');
const path = require('path');
const solc = require('solc');

const contractsDir = path.join(__dirname, 'contracts');
const artifactsDir = path.join(__dirname, 'artifacts');
const nodeModulesDir = path.join(__dirname, 'node_modules');

function findImports(importPath) {
  let fullPath;
  if (importPath.startsWith('@openzeppelin/')) {
    fullPath = path.join(nodeModulesDir, importPath);
  } else if (importPath.startsWith('./') || importPath.startsWith('../')) {
    fullPath = path.join(contractsDir, importPath);
  } else {
    fullPath = path.join(contractsDir, importPath);
  }

  if (fs.existsSync(fullPath)) {
    return { contents: fs.readFileSync(fullPath, 'utf8') };
  } else {
    return { error: 'File not found: ' + importPath + ' (searched at: ' + fullPath + ')' };
  }
}

const contractFiles = fs.readdirSync(contractsDir).filter(f => f.endsWith('.sol'));

const sources = {};
for (const file of contractFiles) {
  const filePath = path.join(contractsDir, file);
  sources[file] = {
    content: fs.readFileSync(filePath, 'utf8')
  };
}

const input = {
  language: 'Solidity',
  sources: sources,
  settings: {
    viaIR: true,
    optimizer: {
      enabled: true,
      runs: 200
    },
    outputSelection: {
      '*': {
        '*': ['abi', 'evm.bytecode.object', 'evm.deployedBytecode.object', 'metadata']
      }
    }
  }
};

console.log('Compiling contracts:', contractFiles.join(', '));
const output = JSON.parse(solc.compile(JSON.stringify(input), { import: findImports }));

let hasErrors = false;
if (output.errors) {
  for (const err of output.errors) {
    if (err.severity === 'error') {
      console.error(err.formattedMessage);
      hasErrors = true;
    } else {
      console.warn(err.formattedMessage);
    }
  }
}

if (hasErrors) {
  console.error('Compilation failed due to errors.');
  process.exit(1);
}

// Ensure artifacts directories exist
if (!fs.existsSync(artifactsDir)) {
  fs.mkdirSync(artifactsDir, { recursive: true });
}

const compiledContracts = {};

for (const sourceName in output.contracts) {
  const sourceContracts = output.contracts[sourceName];
  for (const contractName in sourceContracts) {
    const contractData = sourceContracts[contractName];
    const abi = contractData.abi;
    const bytecode = '0x' + contractData.evm.bytecode.object;
    const deployedBytecode = '0x' + contractData.evm.deployedBytecode.object;

    // Standard Hardhat format
    const artifact = {
      _format: 'hh-sol-artifact-1',
      contractName: contractName,
      sourceName: 'contracts/' + sourceName,
      abi: abi,
      bytecode: bytecode,
      deployedBytecode: deployedBytecode,
      linkReferences: {},
      deployedLinkReferences: {}
    };

    const targetSubdir = path.join(artifactsDir, 'contracts', sourceName);
    if (!fs.existsSync(targetSubdir)) {
      fs.mkdirSync(targetSubdir, { recursive: true });
    }

    const artifactPath = path.join(targetSubdir, `${contractName}.json`);
    fs.writeFileSync(artifactPath, JSON.stringify(artifact, null, 2), 'utf8');
    console.log(`Saved artifact: ${contractName} -> ${artifactPath}`);

    compiledContracts[contractName] = {
      sourceName,
      artifactPath,
      abi,
      bytecode
    };
  }
}

// Also write a consolidated contract-defs.json for easy consumption by Python web3 and scripts
const summaryPath = path.join(artifactsDir, 'contract_artifacts.json');
fs.writeFileSync(summaryPath, JSON.stringify(compiledContracts, null, 2), 'utf8');
console.log(`\nSuccessfully compiled ${Object.keys(compiledContracts).length} contracts/interfaces.`);
console.log(`Consolidated manifest written to ${summaryPath}`);
