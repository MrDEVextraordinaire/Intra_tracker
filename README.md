The root problem is clear now — everything cascades from NumPy 2.x breaking ABI compatibility with system-compiled packages (matplotlib, scipy, etc.). The cleanest fix is to downgrade NumPy to 1.x rather than fighting each broken system package one by one.
bashpip install --user "numpy<2"
Then verify:
bashpython3 -c "import numpy; print(numpy.__version__)"
# Should print 1.26.x or similar
Then retry your script. If it still pulls the wrong numpy, you may need to explicitly uninstall the 2.x version first:
bashpip uninstall numpy
pip install --user "numpy<2"
Why this is the right fix: You have multiple system packages (matplotlib, scipy, missingno via dtale) all compiled against NumPy 1.x. Upgrading all of them isn't practical on a machine where you don't have root, and the system versions will keep interfering. Pinning numpy to <2 is the pragmatic solution until dtale and its deps officially support NumPy 2.
If you want to be more precise about what dtale needs, you can also pin everything together:
bashpip install --user "numpy<2" "scipy>=1.7" "matplotlib>=3.5" dtale
