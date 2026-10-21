"""Small bounded caches shared by document renderers."""
from collections import OrderedDict


class RenderCache:
    def __init__(self,max_items=1024,max_cost=1_000_000):
        self.max_items=max_items;self.max_cost=max_cost
        self.entries=OrderedDict();self.cost=0

    def get(self,key):
        entry=self.entries.get(key)
        if entry is None:return None
        self.entries.move_to_end(key)
        return entry[0]

    def put(self,key,value,cost=1):
        old=self.entries.pop(key,None)
        if old:self.cost-=old[1]
        if cost>self.max_cost:return value
        self.entries[key]=(value,cost);self.cost+=cost
        while len(self.entries)>self.max_items or self.cost>self.max_cost:
            _,(_,removed)=self.entries.popitem(last=False);self.cost-=removed
        return value

    def clear(self):
        self.entries.clear();self.cost=0
