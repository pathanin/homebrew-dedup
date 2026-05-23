class Dedup < Formula
  desc "Local browser UI for reviewing and trashing duplicate files"
  homepage "https://github.com/pathanin/homebrew-dedup"
  url "https://github.com/pathanin/homebrew-dedup/archive/refs/tags/v0.1.1.tar.gz"
  sha256 "00e439fb044475116aee0a5d7269d8e192e3b9a33539efa9044afa60ac1a74c6"
  license "MIT"

  depends_on "python@3.12"

  def install
    libexec.install "dedup.py"

    (bin/"dedup").write <<~EOS
      #!/bin/bash
      exec "#{Formula["python@3.12"].opt_bin}/python3.12" -B "#{libexec}/dedup.py" "$@"
    EOS
  end

  test do
    system bin/"dedup", "--help"
  end
end