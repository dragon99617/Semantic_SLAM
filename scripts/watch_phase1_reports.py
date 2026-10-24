#!/usr/bin/env python3
"""Update reviewable phase-one results after each newly evaluated run."""
import time
from summarize_phase1 import build
from write_phase1_report import render


def main():
    last=None
    while True:
        result=build()
        key=tuple((r['id'],r.get('evaluated',False)) for r in result['geometry'])
        if key!=last:
            render(result)
            print(result['updated_at'],result['status'],sum(r.get('evaluated',False) for r in result['geometry']),flush=True)
            last=key
        if result['status']=='completed_available_dataset_scope':break
        time.sleep(45)


if __name__=='__main__':main()
