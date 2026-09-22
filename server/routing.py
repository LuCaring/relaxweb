"""显式合并消息处理入口；重复注册在启动时直接报错。"""


def merge_handlers(*groups):
    handlers = {}
    for group in groups:
        duplicates = handlers.keys() & group.keys()
        if duplicates:
            raise ValueError(f"重复的消息类型：{', '.join(sorted(duplicates))}")
        handlers.update(group)
    return handlers
