@echo off
netsh advfirewall firewall add rule name="EBS VideoCall UDP 12000-12003" dir=in action=allow protocol=UDP localport=12000-12003
netsh advfirewall firewall add rule name="EBS VideoCall TCP File 12100-12101" dir=in action=allow protocol=TCP localport=12100-12101
pause
