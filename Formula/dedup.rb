class Dedup < Formula
  desc "Local browser UI for reviewing and trashing duplicate files"
  homepage "https://github.com/pathanin/homebrew-dedup"
  url "https://github.com/pathanin/homebrew-dedup/archive/refs/tags/v0.1.2.tar.gz"
  sha256 "d17c2b33cb94b470f7ca0187c8ffa9b8d876720da8f18065f6800764ca58478a"
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