'use strict';
const fs=require('node:fs');
require('./calculator.js');
const input=JSON.parse(fs.readFileSync(0,'utf8'));
try {
  process.stdout.write(JSON.stringify(BankResetCore.calculate(input.model,input.options)));
} catch (error) {
  process.stderr.write(error.code === 'MISSING_NORMAL_PROFILE' ? error.message : error.stack);
  process.exitCode = 1;
}
