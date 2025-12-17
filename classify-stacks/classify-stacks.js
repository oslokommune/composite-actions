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
  return { sequential, parallel };
}

function expandGlob(pattern) {
  const glob = require('path');
  const results = [];

  // Simple glob expansion using fs
  function walkDir(dir, basePattern) {
    try {
      const entries = fs.readdirSync(dir, { withFileTypes: true });
      for (const entry of entries) {
        const fullPath = path.join(dir, entry.name);
        if (entry.isDirectory()) {
          results.push(fullPath);
          walkDir(fullPath, basePattern);
        }
      }
    } catch {
      // Directory doesn't exist or not readable
    }
  }

  // Start from current directory
  walkDir('.', pattern);

  // Filter results by glob pattern
  const regex = globToRegex(pattern);
  return results.filter(p => regex.test(p));
}

function getStacksFromGlob(globFilter, validatorMode) {
  if (!globFilter || !globFilter.trim()) {
    return [];
  }

  const patterns = globFilter.split(',').map(p => p.trim()).filter(p => p);
  const allDirs = new Set();

  for (const pattern of patterns) {
    const matches = expandGlob(pattern);
    matches.forEach(m => allDirs.add(m));
  }

  return Array.from(allDirs)
    .filter(dir => isValidStack(dir, validatorMode))
    .sort();
}

function main() {
  const changedFiles = parseChangedFiles(process.env.CHANGED_FILES);
  const globFilter = process.env.GLOB_FILTER || '';
  const environments = parseEnvironments(process.env.ENVIRONMENTS);
  const patterns = parsePatterns(process.env.PATTERNS);
  const validatorMode = process.env.STACK_VALIDATOR || 'backend-s3';

  console.error('Glob filter:', globFilter || '(none)');
  console.error('Changed files:', changedFiles.length);
  console.error('Environments:', Object.keys(environments).join(', '));
  console.error('Patterns:', patterns.length);
  console.error('Validator mode:', validatorMode);

  let validStacks;

  if (globFilter) {
    // Workflow dispatch mode: expand glob pattern
    validStacks = getStacksFromGlob(globFilter, validatorMode);
    console.error('Stacks from glob:', validStacks.length);
  } else {
    // PR/push mode: use changed files
    const directories = filesToDirectories(changedFiles);
    console.error('Directories from changed files:', directories.length);
    validStacks = directories.filter(dir => isValidStack(dir, validatorMode));
  }

  console.error('Valid stacks:', validStacks.length);
  if (validStacks.length > 0) {
    console.error('Stacks:', validStacks.join(', '));
  }

  // Split by environment
  const stacksByEnv = splitByEnvironment(validStacks, environments);

  // Build stages per environment: {sequential: [...], parallel: [...]}
  const result = {};
  for (const [envName, envStacks] of Object.entries(stacksByEnv)) {
    result[envName] = buildStagesForEnv(envStacks, patterns);
  }

  // Ensure all configured environments have entries (even if empty)
  for (const envName of Object.keys(environments)) {
    if (!result[envName]) {
      result[envName] = { sequential: [], parallel: [] };
    }
  }

  const allStacks = validStacks.sort();
  const hasChanges = allStacks.length > 0;

  // Write outputs - one per environment plus metadata
  for (const [envName, stages] of Object.entries(result)) {
    console.log(`${envName}=${JSON.stringify(stages)}`);
  }
  console.log(`all-stacks=${JSON.stringify(allStacks)}`);
  console.log(`has-changes=${hasChanges}`);
}

main();
