'use strict';
const fs=require('node:fs');
require('./calculator.js');
const input=JSON.parse(fs.readFileSync(0,'utf8'));
process.stdout.write(JSON.stringify(BankResetCore.calculate(input.model,input.options)));
