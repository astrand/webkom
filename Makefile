#
# Run "make pycheck" to run pychecker.
#

all:

# Pychecker section
PYTHON = python2
PYCHECKER = pychecker --stdlib
SRCS = acceptlang.py apache-setup.py consoleserver.py kom.py set_version.py \
thfcgi.py TranslatorCache.py webkom_constants.py \
webkom_js.py webkom.py webkom_utils.py
CHECKS=$(patsubst %.py,.%.chk,$(SRCS))


.PHONY: pycheck

pycheck: $(CHECKS)

.%.chk: %.py
	$(PYCHECKER) $^ | tee $@

clean: 
	rm -f $(CHECKS) $(wildcard *.pyc)
