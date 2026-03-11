# Remove Docker proxy - fix "TLS connect to 127.0.0.1:8800: EOF"
# Run this if build fails with proxy errors

$scope = "User"

[System.Environment]::SetEnvironmentVariable("HTTP_PROXY", $null, $scope)
[System.Environment]::SetEnvironmentVariable("HTTPS_PROXY", $null, $scope)
[System.Environment]::SetEnvironmentVariable("NO_PROXY", $null, $scope)

Write-Host "Proxy removed. Restart Docker Desktop, then run .\deploy\pack.ps1"
