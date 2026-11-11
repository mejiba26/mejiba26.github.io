#!/bin/sh
# Fails (exit 1) if any staged path looks like generated sample data.
! git diff --cached --name-only | grep -E 'pdata/|vdata/|paper-sample|vol-sample|SAMPLE_DATA' && echo "ok: no sample files staged"
