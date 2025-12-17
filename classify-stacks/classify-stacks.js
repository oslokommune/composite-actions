#!/usr/bin/env node
const fs = require('fs');
const path = require('path');

const DEFAULT_PATTERNS = [
  '**/remote-state',
  '**/networking-data',
  '**/networking',
  '**/dns',
  '**/certificates',
  '**/load-balancing-*-data',
  '**/load-balancing-*',
  '**/iam',
  '**/app-common',
  '**/datadog-common',
  '**/databases',
  '**/rds-bastion',
  '**/*-data'
];

function parsePatterns(patternsInput) {
  if (!patternsInput || !patternsInput.trim()) {
    return DEFAULT_PATTERNS;
  }
  return patternsInput
    .split('\n')
    .map(line => line.trim())
    .filter(line => line.length > 0);
}

function parseEnvironments(envsInput) {
  try {
    return JSON.parse(envsInput || '{"dev": "dev", "prod": "prod"}');
  } catch {
    console.error('Failed to parse environments, using defaults');
    return { dev: 'dev', prod: 'prod' };
  }
}

function parseChangedFiles(filesInput) {
  try {
    return JSON.parse(filesInput || '[]');
  } catch {
    console.error('Failed to parse changed-files');
    return [];
  }
}

function filesToDirectories(files) {
  const dirs = new Set();
  for (const file of files) {
    const dir = path.dirname(file);
    if (dir && dir !== '.') {
      dirs.add(dir);
    }
  }
  return Array.from(dirs).sort();
}

function isValidStack(dir, validatorMode) {
  if (validatorMode === 'none') {
    return true;
  }

  const tfFiles = [];
  try {
    const entries = fs.readdirSync(dir, { withFileTypes: true });
    for (const entry of entries) {
      if (entry.isFile() && entry.name.endsWith('.tf')) {
        tfFiles.push(path.join(dir, entry.name));
      }
    }
  } catch {
    return false;
  }

  if (tfFiles.length === 0) {
    return false;
  }

  if (validatorMode === 'has-tf') {
    return true;
  }

  // Default: backend-s3
  for (const tfFile of tfFiles) {
    try {
      const content = fs.readFileSync(tfFile, 'utf8');
      if (content.includes('backend "s3"')) {
        return true;
      }
    } catch {
      continue;
    }
  }
  return false;
}

function globToRegex(pattern) {
  let regex = pattern
    .replace(/[.+^${}()|[\]\\]/g, '\\$&') // Escape special regex chars (except * and ?)
    .replace(/\*\*/g, '{{GLOBSTAR}}')     // Placeholder for **
    .replace(/\*/g, '[^/]*')              // * matches anything except /
    .replace(/\?/g, '[^/]')               // ? matches single char except /
    .replace(/{{GLOBSTAR}}/g, '.*');      // ** matches anything including /
  return new RegExp(`^${regex}$`);
}

function matchesPattern(stackPath, pattern) {
  const regex = globToRegex(pattern);
  return regex.test(stackPath);
}

function findFirstMatchingPattern(stack, patterns) {
  for (let i = 0; i < patterns.length; i++) {
    if (matchesPattern(stack, patterns[i])) {
      return i;
    }
  }
  return null;
}

function classifyStacks(stacks, patterns) {
  const buckets = patterns.map(() => []);
  const parallel = [];

  for (const stack of stacks) {
    const patternIndex = findFirstMatchingPattern(stack, patterns);
    if (patternIndex !== null) {
      buckets[patternIndex].push(stack);
    } else {
      parallel.push(stack);
    }
  }

  // Flatten buckets into sequential array (preserving pattern order)
  const sequential = buckets.flat();

  return { sequential, parallel };
}

function splitByEnvironment(stacks, environments) {
  const result = {};
  for (const [envName, prefix] of Object.entries(environments)) {
    result[envName] = stacks.filter(stack => stack.startsWith(prefix + '/') || stack === prefix);
  }
  return result;
}

function buildStagesForEnv(stacks, patterns) {
  const { sequential, parallel } = classifyStacks(stacks, patterns);

  const stages = [];
  if (sequential.length > 0) {
    stages.push({ name: 'sequential', stacks: sequential, parallel: false });
  }
  if (parallel.length > 0) {
    stages.push({ name: 'parallel', stacks: parallel, parallel: true });
  }
  return stages;
}

function main() {
  const changedFiles = parseChangedFiles(process.env.CHANGED_FILES);
  const environments = parseEnvironments(process.env.ENVIRONMENTS);
  const patterns = parsePatterns(process.env.PATTERNS);
  const validatorMode = process.env.STACK_VALIDATOR || 'backend-s3';

  console.error('Changed files:', changedFiles.length);
  console.error('Environments:', Object.keys(environments).join(', '));
  console.error('Patterns:', patterns.length);
  console.error('Validator mode:', validatorMode);

  // Extract directories from changed files
  const directories = filesToDirectories(changedFiles);
  console.error('Directories from changed files:', directories.length);

  // Filter to valid Terraform stacks
  const validStacks = directories.filter(dir => isValidStack(dir, validatorMode));
  console.error('Valid stacks:', validStacks.length);

  if (validStacks.length > 0) {
    console.error('Stacks:', validStacks.join(', '));
  }

  // Split by environment
  const stacksByEnv = splitByEnvironment(validStacks, environments);

  // Build stages per environment
  const stagesByEnv = {};
  for (const [envName, envStacks] of Object.entries(stacksByEnv)) {
    stagesByEnv[envName] = buildStagesForEnv(envStacks, patterns);
  }

  // Output results
  const allStacks = validStacks.sort();
  const hasChanges = allStacks.length > 0;

  // Write to GITHUB_OUTPUT format
  console.log(`dev-stages=${JSON.stringify(stagesByEnv.dev || [])}`);
  console.log(`prod-stages=${JSON.stringify(stagesByEnv.prod || [])}`);
  console.log(`all-stacks=${JSON.stringify(allStacks)}`);
  console.log(`has-changes=${hasChanges}`);
}

main();
