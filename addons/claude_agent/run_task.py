"""Retired unsupervised demonstration entry point.

The old runner lacked the production host ledger, current permission bindings
and Windows Job ownership. It must not launch a CLI or supply auto-answers.
Use Alice's authorized GUI/public ChatService worker interfaces for manual
acceptance. Historical demo observations remain in their original documents.
"""

def main() -> int:
    print('此旧裸runner已退役：不会启动CLI、写任务目录或自动答复。请使用Alice受监督公共协作入口。')
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
