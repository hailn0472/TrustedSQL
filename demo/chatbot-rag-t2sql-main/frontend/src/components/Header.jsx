import React, { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';

const Header = () => {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [showGuide, setShowGuide] = useState(false);

  const handleLogout = () => {
    logout();
    navigate('/login');
  };

  return (
    <header className="relative bg-gradient-to-r from-[#4A3F71] to-[#5E507F] px-8 py-5 flex items-center justify-between">
      {/* Texture overlay */}
      <div className="absolute inset-0 bg-[url('data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iNjAiIGhlaWdodD0iNjAiIHhtbG5zPSJodHRwOi8vd3d3LnczLm9yZy8yMDAwL3N2ZyI+PGRlZnM+PHBhdHRlcm4gaWQ9ImdyaWQiIHdpZHRoPSI2MCIgaGVpZ2h0PSI2MCIgcGF0dGVyblVuaXRzPSJ1c2VyU3BhY2VPblVzZSI+PHBhdGggZD0iTSAxMCAwIEwgMCAwIDAgMTAiIGZpbGw9Im5vbmUiIHN0cm9rZT0id2hpdGUiIHN0cm9rZS13aWR0aD0iMC41Ii8+PC9wYXR0ZXJuPjwvZGVmcz48cmVjdCB3aWR0aD0iMTAwJSIgaGVpZ2h0PSIxMDAlIiBmaWxsPSJ1cmwoI2dyaWQpIi8+PC9zdmc+')] opacity-5 pointer-events-none"></div>
      
      {/* Bottom border gradient */}
      <div className="absolute bottom-0 left-0 right-0 h-px bg-gradient-to-r from-transparent via-white/20 to-transparent"></div>
      
      {/* Branding */}
      <div className="relative flex items-center gap-3 z-10">
        <div className="relative flex items-center gap-2">
          <h1 className="text-white text-xl font-semibold">FARIS</h1>
          {/* Info icon */}
          <button
            onClick={() => setShowGuide(true)}
            className="text-white/80 hover:text-white hover:bg-white/10 rounded-full p-1.5 transition-all duration-200"
            aria-label="Hướng dẫn sử dụng"
            title="Hướng dẫn sử dụng"
          >
            <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 20 20">
              <path fillRule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a1 1 0 000 2v3a1 1 0 001 1h1a1 1 0 100-2v-3a1 1 0 00-1-1H9z" clipRule="evenodd" />
            </svg>
          </button>
          {/* Teal accent bar */}
          <div className="absolute -left-2 top-1/2 -translate-y-1/2 w-1.5 h-6 bg-teal-400 rounded-full"></div>
        </div>
      </div>
      
      {/* Navigation */}
      <nav className="relative z-10">
        <ul className="flex items-center gap-6">
          <li>
            <Link 
              to="/" 
              className="text-white text-sm font-medium px-4 py-2 rounded-lg bg-white/10 transition-all duration-200"
            >
              CHAT
            </Link>
          </li>
          {user && (
            <>
              <li>
                <span className="text-white/70 text-sm">
                  Welcome, {user.username}
                </span>
              </li>
              <li>
                <button
                  onClick={handleLogout}
                  className="text-white text-sm font-medium px-4 py-2 rounded-lg hover:bg-red-500/20 transition-all duration-200 border border-white/20"
                >
                  LOGOUT
                </button>
              </li>
            </>
          )}
        </ul>
      </nav>

      {/* Guide Modal */}
      {showGuide && (
        <div 
          className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4"
          onClick={() => setShowGuide(false)}
        >
          <div 
            className="bg-white rounded-2xl shadow-2xl max-w-2xl w-full max-h-[80vh] overflow-y-auto"
            onClick={(e) => e.stopPropagation()}
          >
            {/* Modal Header */}
            <div className="sticky top-0 bg-gradient-to-r from-[#4A3F71] to-[#5E507F] px-6 py-4 flex items-center justify-between rounded-t-2xl">
              <h2 className="text-xl font-bold text-white flex items-center gap-2">
                <svg className="w-6 h-6" fill="currentColor" viewBox="0 0 20 20">
                  <path d="M9 4.804A7.968 7.968 0 005.5 4c-1.255 0-2.443.29-3.5.804v10A7.969 7.969 0 015.5 14c1.669 0 3.218.51 4.5 1.385A7.962 7.962 0 0114.5 14c1.255 0 2.443.29 3.5.804v-10A7.968 7.968 0 0014.5 4c-1.255 0-2.443.29-3.5.804V12a1 1 0 11-2 0V4.804z" />
                </svg>
                Hướng Dẫn Sử Dụng Chatbot
              </h2>
              <button
                onClick={() => setShowGuide(false)}
                className="text-white/80 hover:text-white hover:bg-white/10 rounded-full p-2 transition-all duration-200"
                aria-label="Đóng"
              >
                <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>

            {/* Modal Content */}
            <div className="p-6 space-y-6">
              {/* Introduction */}
              <div className="bg-teal-50 border-l-4 border-teal-400 p-4 rounded-r-lg">
                <p className="text-gray-700 leading-relaxed">
                  <strong className="text-teal-700">FARIS (FPT AI Research & Information System)</strong> là trợ lý thông minh giúp bạn truy vấn dữ liệu và tìm kiếm thông tin từ tài liệu một cách dễ dàng.
                </p>
              </div>

              {/* Features */}
              <div>
                <h3 className="text-lg font-bold text-gray-800 mb-3 flex items-center gap-2">
                  <span className="text-2xl">✨</span>
                  Tính Năng Chính
                </h3>
                <div className="space-y-3">
                  <div className="flex gap-3 items-start">
                    <span className="text-2xl flex-shrink-0">🔍</span>
                    <div>
                      <strong className="text-gray-800">Truy vấn SQL thông minh:</strong>
                      <p className="text-gray-600 text-sm mt-1">Hỏi về dữ liệu bằng ngôn ngữ tự nhiên, chatbot sẽ tự động chuyển đổi thành câu SQL và trả về kết quả.</p>
                    </div>
                  </div>
                  <div className="flex gap-3 items-start">
                    <span className="text-2xl flex-shrink-0">📚</span>
                    <div>
                      <strong className="text-gray-800">Tìm kiếm tài liệu (RAG):</strong>
                      <p className="text-gray-600 text-sm mt-1">Tìm kiếm thông tin từ kho tài liệu kiến thức với độ chính xác cao.</p>
                    </div>
                  </div>
                  <div className="flex gap-3 items-start">
                    <span className="text-2xl flex-shrink-0">💬</span>
                    <div>
                      <strong className="text-gray-800">Trò chuyện tự nhiên:</strong>
                      <p className="text-gray-600 text-sm mt-1">Trả lời các câu hỏi chung và hỗ trợ đa dạng chủ đề.</p>
                    </div>
                  </div>
                </div>
              </div>

              {/* How to use */}
              <div>
                <h3 className="text-lg font-bold text-gray-800 mb-3 flex items-center gap-2">
                  <span className="text-2xl">📝</span>
                  Cách Sử Dụng
                </h3>
                <ol className="space-y-3 list-decimal list-inside text-gray-700">
                  <li className="pl-2">
                    <strong>Nhập câu hỏi</strong> vào ô chat ở phía dưới màn hình
                  </li>
                  <li className="pl-2">
                    <strong>Nhấn Enter</strong> hoặc click nút gửi để gửi câu hỏi
                  </li>
                  <li className="pl-2">
                    <strong>Xem quá trình xử lý</strong> ở sidebar bên trái (Thinking Process)
                  </li>
                  <li className="pl-2">
                    <strong>Theo dõi luồng hệ thống</strong> ở sidebar bên phải (System Architecture)
                  </li>
                  <li className="pl-2">
                    <strong>Nhận câu trả lời</strong> chi tiết từ chatbot
                  </li>
                </ol>
              </div>

              

              {/* Tips */}
              <div className="bg-amber-50 border-l-4 border-amber-400 p-4 rounded-r-lg">
                <h3 className="text-sm font-bold text-amber-800 mb-2 flex items-center gap-2">
                  <span>💡</span>
                  Mẹo Sử Dụng Hiệu Quả
                </h3>
                <ul className="space-y-1 text-sm text-amber-900">
                  <li>• Đặt câu hỏi rõ ràng và cụ thể</li>
                  <li>• Sử dụng ngôn ngữ tự nhiên, không cần biết SQL</li>
                  <li>• Theo dõi sidebar để hiểu cách chatbot xử lý câu hỏi</li>
                </ul>
              </div>
            </div>

            {/* Modal Footer */}
            <div className="sticky bottom-0 bg-gray-50 px-6 py-4 flex justify-end rounded-b-2xl border-t border-gray-200">
              <button
                onClick={() => setShowGuide(false)}
                className="bg-gradient-to-r from-[#5E507F] to-[#4A3F71] text-white px-6 py-2 rounded-lg font-medium hover:shadow-lg transition-all duration-200"
              >
                Đã Hiểu
              </button>
            </div>
          </div>
        </div>
      )}
    </header>
  );
};

export default Header;
