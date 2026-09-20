/**
 * Read-only access to CERN's institutional component evidence.
 */

import { execFile } from 'child_process';
import { existsSync } from 'fs';
import { dirname, join } from 'path';
import { fileURLToPath } from 'url';
import { promisify } from 'util';
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { z } from 'zod';
import { logger } from '../logger.js';

const execFileAsync = promisify(execFile);
const moduleDirectory = dirname(fileURLToPath(import.meta.url));
const projectRoot = join(moduleDirectory, '..', '..');
const defaultDatabase = join(projectRoot, 'CERN.sqlite');

function pythonExecutable(): string {
  return process.env.KICAD_PYTHON || (process.platform === 'win32' ? 'python.exe' : 'python3');
}

/**
 * Register tools that search CERN evidence without changing the source snapshot.
 */
export function registerCernTools(server: McpServer): void {
  logger.info('Registering CERN evidence tools');

  server.tool(
    'search_cern_components',
    'Search read-only CERN institutional component evidence. Results are unverified source assertions, not manufacturer truth or an automatic part recommendation.',
    {
      query: z.string().min(1).max(250).describe('Text to find in CERN source fields'),
      limit: z.number().int().min(1).max(100).optional()
        .describe('Maximum number of matching source records to return (default: 25)'),
      sqlitePath: z.string().optional()
        .describe('Path to a CERN SQLite snapshot (default: the bundled CERN.sqlite)'),
    },
    async ({ query, limit = 25, sqlitePath = defaultDatabase }) => {
      if (!existsSync(sqlitePath)) {
        return {
          content: [{
            type: 'text',
            text: JSON.stringify({ error: `CERN SQLite snapshot not found: ${sqlitePath}` }),
          }],
          isError: true,
        };
      }

      try {
        const { stdout } = await execFileAsync(
          pythonExecutable(),
          ['-m', 'adapters.cern.catalog', '--sqlite', sqlitePath, '--query', query, '--limit', String(limit)],
          { cwd: projectRoot, timeout: 30_000, maxBuffer: 1_048_576 },
        );
        return {
          content: [{ type: 'text', text: stdout }],
        };
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        logger.error(`CERN catalog search failed: ${message}`);
        return {
          content: [{ type: 'text', text: JSON.stringify({ error: `CERN catalog search failed: ${message}` }) }],
          isError: true,
        };
      }
    },
  );
}
