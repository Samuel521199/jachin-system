# Set Docker proxy (port 8800)
# Run as Administrator if setting Machine-level env vars

$proxy = "http://127.0.0.1:8800"
$scope = "User"  # Change to "Machine" for system-wide (requires Admin)

# NO_PROXY: bypass proxy for localhost and daocloud mirror (build uses daocloud)
$noProxy = "localhost,127.0.0.1,docker.m.daocloud.io,*.daocloud.io"

[System.Environment]::SetEnvironmentVariable("HTTP_PROXY", $proxy, $scope)
[System.Environment]::SetEnvironmentVariable("HTTPS_PROXY", $proxy, $scope)
[System.Environment]::SetEnvironmentVariable("NO_PROXY", $noProxy, $scope)

Write-Host "Proxy set: $proxy"
Write-Host "NO_PROXY: $noProxy"
Write-Host "Restart Docker Desktop for changes to take effect."
