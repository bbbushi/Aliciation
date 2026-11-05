"""Legacy migration command retired: core WorkerLedger owns its additive schema.

No addon may open Alice's live database. Start the approved service version only
in an explicitly authorized deployment; preserve the existing policy and ledger.
"""
if __name__ == '__main__':
    raise SystemExit('迁移已归核心启动流程；此旧入口不再读写数据库。请按部署/备份方案更新。')
