"""Fixed-domain CPU photograph fit, recipe persistence and borrowed native replay."""
def fit(pair,request,*,cancelled=lambda:False,progress=None):
    from .executor import fit as execute
    return execute(pair,request,cancelled=cancelled,progress=progress)

def save_recipe(recipe,directory):
    from .recipe import save_recipe as save
    return save(recipe,directory)

def load_recipe(directory):
    from .recipe import load_recipe as load
    return load(directory)

def render(source,recipe_directory,request,*,out,cancelled=lambda:False,progress=None):
    from .renderer import render as execute
    return execute(source,recipe_directory,request,out=out,cancelled=cancelled,progress=progress)
